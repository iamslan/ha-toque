"""Toque: a Monsieur Cuisine Connect robot running the Toque app, read-only."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ToqueClient
from .const import CONF_MODEL, CONF_SW_VERSION, DEFAULT_PORT, DOMAIN, MANUFACTURER
from .coordinator import ToqueConfigEntry, ToqueCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once, whatever the number of robots."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ToqueConfigEntry) -> bool:
    """Set up one robot.

    The robot is not contacted here: it is often switched off when Home
    Assistant starts, and that must not fail or delay the setup. The
    entities start unavailable and come alive with the first status.
    """
    client = ToqueClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data[CONF_TOKEN],
    )
    coordinator = ToqueCoordinator(hass, entry, client)

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        manufacturer=MANUFACTURER,
        name=entry.data.get(CONF_NAME) or entry.title,
        model=entry.data.get(CONF_MODEL),
        sw_version=entry.data.get(CONF_SW_VERSION),
    )
    coordinator.device_id = device.id

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Started last, so that the event entity is already listening.
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ToqueConfigEntry) -> bool:
    """Unload one robot; the event stream task is cancelled with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
