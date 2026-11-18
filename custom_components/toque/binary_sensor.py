"""Binary sensors of a Toque robot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ToqueConfigEntry, ToqueCoordinator
from .entity import ToqueEntity, sub

# The coordinator is push-driven: nothing here polls.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ToqueBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor and how to read it in the status object."""

    is_on_fn: Callable[[dict[str, Any]], bool | None]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _flag(value: Any) -> bool | None:
    """A JSON boolean, or None (unknown) for null or anything else."""
    return value if isinstance(value, bool) else None


def _waiting_for_user(status: dict[str, Any]) -> bool:
    return status.get("state") == "waiting_for_user" or sub(status, "guided", "waiting_for_user") is True


def _fault_attributes(status: dict[str, Any]) -> dict[str, Any]:
    fault = status.get("fault")
    if not isinstance(fault, dict):
        return {"code": None, "message": None}
    return {"code": fault.get("code"), "message": fault.get("message")}


BINARY_SENSORS: tuple[ToqueBinarySensorDescription, ...] = (
    ToqueBinarySensorDescription(
        key="lid_open",
        translation_key="lid_open",
        device_class=BinarySensorDeviceClass.OPENING,
        is_on_fn=lambda s: _flag(s.get("lid_open")),
    ),
    ToqueBinarySensorDescription(
        key="bowl_present",
        translation_key="bowl_present",
        # null when the bowl sensor is not reliable: the state is then unknown.
        is_on_fn=lambda s: _flag(s.get("bowl_present")),
        attributes_fn=lambda s: {"sensor_reliable": s.get("bowl_sensor_reliable")},
    ),
    ToqueBinarySensorDescription(
        key="motor_running",
        translation_key="motor_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda s: _flag(s.get("motor_on")),
    ),
    ToqueBinarySensorDescription(
        key="heating",
        translation_key="heating",
        device_class=BinarySensorDeviceClass.HEAT,
        is_on_fn=lambda s: _flag(s.get("heater_on")),
    ),
    ToqueBinarySensorDescription(
        key="waiting_for_user",
        translation_key="waiting_for_user",
        is_on_fn=_waiting_for_user,
    ),
    ToqueBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda s: s.get("fault") is not None or s.get("state") == "fault",
        attributes_fn=_fault_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToqueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the binary sensors of one robot."""
    coordinator = entry.runtime_data
    async_add_entities(
        ToqueBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class ToqueBinarySensor(ToqueEntity, BinarySensorEntity):
    """One yes/no value of the robot's status."""

    entity_description: ToqueBinarySensorDescription

    def __init__(
        self, coordinator: ToqueCoordinator, description: ToqueBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """True, False, or None (unknown) when the robot reports null."""
        status = self.coordinator.data
        if status is None:
            return None
        return self.entity_description.is_on_fn(status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra details that go with the value."""
        status = self.coordinator.data
        if status is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(status)
