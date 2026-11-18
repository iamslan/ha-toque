"""Config flow for Toque: discovery or manual entry, then pairing by code."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_CODE, CONF_HOST, CONF_NAME, CONF_PORT, CONF_TOKEN
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    PAIRING_ERROR_CODES,
    ToqueClient,
    ToqueConnectionError,
    ToqueError,
    ToquePairingError,
)
from .const import (
    CONF_MODEL,
    CONF_SW_VERSION,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
    PAIRING_NAME,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
    }
)
PAIR_SCHEMA = vol.Schema({vol.Required(CONF_CODE): str})

_CODE_RE = re.compile(r"\d{6}")


class ToqueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a robot: find it, then type the code shown on its screen."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._info: dict[str, Any] = {}

    def _client(self) -> ToqueClient:
        return ToqueClient(async_get_clientsession(self.hass), self._host, self._port)

    @property
    def _name(self) -> str:
        name = self._info.get("name")
        return name if isinstance(name, str) and name else DEFAULT_NAME

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual entry of the robot's address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = user_input[CONF_PORT]
            try:
                info = await self._client().info()
            except ToqueConnectionError:
                errors["base"] = "cannot_connect"
            except ToqueError as err:
                _LOGGER.debug("Unexpected answer from %s:%s: %s", self._host, self._port, err)
                errors["base"] = "not_toque"
            else:
                await self.async_set_unique_id(info["id"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: self._host, CONF_PORT: self._port}
                )
                self._info = info
                return await self.async_step_pair()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """A robot announced itself as ``_toque._tcp.local.``."""
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        updates = {CONF_HOST: self._host, CONF_PORT: self._port}

        # The contract does not define TXT records; if the robot publishes
        # its id there, a known robot is recognised without any request.
        txt_id = discovery_info.properties.get("id")
        if isinstance(txt_id, str) and txt_id:
            await self.async_set_unique_id(txt_id)
            self._abort_if_unique_id_configured(updates=updates)

        try:
            info = await self._client().info()
        except ToqueError as err:
            _LOGGER.debug("Discovered %s:%s but /api/info failed: %s", self._host, self._port, err)
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(info["id"])
        self._abort_if_unique_id_configured(updates=updates)
        self._info = info
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_pair()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The robot answered 401: the token was revoked, pair again."""
        self._host = entry_data[CONF_HOST]
        self._port = entry_data.get(CONF_PORT, DEFAULT_PORT)
        self._info = {"name": self._get_reauth_entry().title}
        return await self.async_step_pair()

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the 6-digit code shown on the robot and exchange it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            code = re.sub(r"[\s-]", "", user_input[CONF_CODE])
            if not _CODE_RE.fullmatch(code):
                errors["base"] = "invalid_code"
            else:
                try:
                    result = await self._client().pair(code, PAIRING_NAME)
                except ToquePairingError as err:
                    errors["base"] = err.code if err.code in PAIRING_ERROR_CODES else "unknown"
                except ToqueConnectionError:
                    errors["base"] = "cannot_connect"
                except ToqueError as err:
                    _LOGGER.debug("Pairing with %s:%s failed: %s", self._host, self._port, err)
                    errors["base"] = "unknown"
                else:
                    return await self._async_finish(result)

        return self.async_show_form(
            step_id="pair",
            data_schema=PAIR_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._name, "host": self._host},
        )

    async def _async_finish(self, result: dict[str, Any]) -> ConfigFlowResult:
        """Pairing succeeded: store the token."""
        token: str = result["token"]
        paired_id = result.get("id")

        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if isinstance(paired_id, str) and paired_id != entry.unique_id:
                # Another robot now answers at this address.
                return self.async_abort(reason="wrong_device")
            return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})

        if isinstance(paired_id, str) and paired_id and paired_id != self.unique_id:
            await self.async_set_unique_id(paired_id)
            self._abort_if_unique_id_configured()

        name = result.get("name")
        if not isinstance(name, str) or not name:
            name = self._name
        return self.async_create_entry(
            title=name,
            data={
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_TOKEN: token,
                CONF_NAME: name,
                CONF_MODEL: self._info.get("model"),
                CONF_SW_VERSION: self._info.get("version"),
            },
        )
