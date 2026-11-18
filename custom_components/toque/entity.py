"""Base entity for the Toque integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MAX_STATE_LENGTH
from .coordinator import ToqueCoordinator


def sub(status: dict[str, Any], section: str, key: str) -> Any:
    """Read ``status[section][key]`` where the section may be null."""
    part = status.get(section)
    return part.get(key) if isinstance(part, dict) else None


def short_text(value: Any) -> str | None:
    """A text that fits in a Home Assistant state (255 characters)."""
    if value is None:
        return None
    text = str(value)
    if len(text) > MAX_STATE_LENGTH:
        return text[: MAX_STATE_LENGTH - 1] + "…"
    return text


class ToqueEntity(CoordinatorEntity[ToqueCoordinator]):
    """An entity of one robot, attached to the robot's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ToqueCoordinator, key: str) -> None:
        super().__init__(coordinator)
        robot_id = coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
        self._attr_unique_id = f"{robot_id}_{key}"
        # The device itself is registered in async_setup_entry.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, robot_id)})

    @property
    def available(self) -> bool:
        """Unavailable while the robot is unreachable or was never reached."""
        return super().available and self.coordinator.data is not None
