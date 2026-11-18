"""Push-driven data coordinator for one Toque robot."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import aclosing
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ToqueAuthError, ToqueClient, ToqueError, ToqueEvent
from .const import DOMAIN, EVENT_TOQUE, EVENT_TYPES, RECONNECT_MAX, RECONNECT_MIN

_LOGGER = logging.getLogger(__name__)

type ToqueConfigEntry = ConfigEntry[ToqueCoordinator]

type ToqueEventListener = Callable[[str, dict[str, Any]], None]


class ToqueCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keep the robot's status up to date from its event stream.

    ``data`` is the last ``/api/status`` object, or ``None`` until the robot
    has been reached once. There is no polling interval: a background task
    keeps ``/api/events`` open and, while that stream is down, tries again
    with a backoff and polls ``/api/status`` after each failed attempt.

    A robot that does not answer is normal (it is simply switched off most
    of the day), so that is logged at debug level only.
    """

    config_entry: ToqueConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ToqueConfigEntry, client: ToqueClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=None,
        )
        self.client = client
        self.device_id: str | None = None
        self.stream_connected = False
        self._event_listeners: list[ToqueEventListener] = []
        self._sw_version: str | None = None
        self._auth_failed = False

    # ------------------------------------------------------------ public API

    @callback
    def async_start(self) -> None:
        """Start the background task; it is cancelled when the entry unloads."""
        self.config_entry.async_create_background_task(
            self.hass, self._async_run(), name=f"{DOMAIN} event stream {self.config_entry.title}"
        )

    @callback
    def async_add_event_listener(self, listener: ToqueEventListener) -> CALLBACK_TYPE:
        """Call ``listener(event_type, data)`` for every cooking event."""
        self._event_listeners.append(listener)

        @callback
        def remove() -> None:
            self._event_listeners.remove(listener)

        return remove

    async def _async_update_data(self) -> dict[str, Any]:
        """One explicit poll (only used for a manual refresh request)."""
        try:
            return await self.client.status()
        except ToqueAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ToqueError as err:
            raise UpdateFailed(str(err)) from err

    # ------------------------------------------------------- background task

    async def _async_run(self) -> None:
        """Hold the event stream open for as long as the entry is loaded."""
        backoff = RECONNECT_MIN
        while True:
            try:
                await self._async_consume_stream()
                _LOGGER.debug("%s: event stream closed by the robot", self.name)
            except ToqueAuthError as err:
                self._async_handle_auth_error(err)
                return
            except ToqueError as err:
                _LOGGER.debug("%s: event stream unavailable: %s", self.name, err)
            except Exception:  # noqa: BLE001 - a bug must not end the loop for good
                _LOGGER.exception("%s: unexpected error in the event stream", self.name)

            if self.stream_connected:
                # It worked until now: retry quickly first.
                self.stream_connected = False
                backoff = RECONNECT_MIN

            # Fallback while the stream is down: one status poll per attempt.
            try:
                status = await self.client.status()
            except ToqueAuthError as err:
                self._async_handle_auth_error(err)
                return
            except ToqueError as err:
                if self.last_update_success and self.data is not None:
                    _LOGGER.debug("%s: robot unreachable, probably switched off: %s", self.name, err)
                self.async_set_update_error(err)
            else:
                self._async_set_status(status)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _async_consume_stream(self) -> None:
        """Read events until the stream ends (returns) or fails (raises)."""
        async with aclosing(self.client.events()) as stream:
            async for event in stream:
                if not self.stream_connected:
                    self.stream_connected = True
                    _LOGGER.debug("%s: event stream connected", self.name)
                self._async_handle_event(event)

    @callback
    def _async_handle_event(self, event: ToqueEvent) -> None:
        if event.event == "status":
            if isinstance(event.data, dict):
                self._async_set_status(event.data)
            return
        if event.event not in EVENT_TYPES:
            _LOGGER.debug("%s: ignoring unknown event %s", self.name, event.event)
            return
        data: dict[str, Any] = event.data if isinstance(event.data, dict) else {}
        self.hass.bus.async_fire(
            EVENT_TOQUE,
            {
                **data,
                ATTR_DEVICE_ID: self.device_id,
                "entry_id": self.config_entry.entry_id,
                "type": event.event,
            },
        )
        for listener in list(self._event_listeners):
            listener(event.event, data)

    @callback
    def _async_set_status(self, status: dict[str, Any]) -> None:
        """Publish a new status to the entities."""
        self._async_update_sw_version(status.get("version"))
        self.async_set_updated_data(status)

    @callback
    def _async_update_sw_version(self, version: Any) -> None:
        """Keep the device's software version in step with the robot."""
        if not isinstance(version, str) or version == self._sw_version:
            return
        self._sw_version = version
        if self.device_id is not None:
            dr.async_get(self.hass).async_update_device(self.device_id, sw_version=version)

    @callback
    def _async_handle_auth_error(self, err: ToqueAuthError) -> None:
        """The token was revoked on the robot: ask the user to pair again."""
        if not self._auth_failed:
            self._auth_failed = True
            _LOGGER.warning(
                "%s: the robot refused the token (%s); pairing is needed again", self.name, err
            )
            self.config_entry.async_start_reauth(self.hass)
        self.async_set_update_error(err)
