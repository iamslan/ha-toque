"""Event entity of a Toque robot: one entity for every cooking event."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_TYPES
from .coordinator import ToqueConfigEntry, ToqueCoordinator
from .entity import ToqueEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToqueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the event entity of one robot."""
    async_add_entities([ToqueCookingEvent(entry.runtime_data)])


class ToqueCookingEvent(ToqueEntity, EventEntity):
    """Fires for every non-status event of the robot's event stream.

    The event's data (time, kind, label, guided, ...) become the attributes.
    """

    _attr_translation_key = "cooking_event"
    _attr_event_types = list(EVENT_TYPES)

    def __init__(self, coordinator: ToqueCoordinator) -> None:
        super().__init__(coordinator, "cooking_event")

    async def async_added_to_hass(self) -> None:
        """Start listening to the robot's events."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_event_listener(self._async_handle_event))

    @callback
    def _async_handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
