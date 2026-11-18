"""Sensors of a Toque robot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfMass,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import JOB_KINDS, LINK_STATES, ROBOT_STATES
from .coordinator import ToqueConfigEntry, ToqueCoordinator
from .entity import ToqueEntity, short_text, sub

# The coordinator is push-driven: nothing here polls.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ToqueSensorDescription(SensorEntityDescription):
    """A sensor and how to read it in the status object."""

    value_fn: Callable[[dict[str, Any]], StateType | datetime]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _number(value: Any) -> int | float | None:
    """Keep numbers only (the contract uses null for unknown)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _end_time(status: dict[str, Any]) -> datetime | None:
    ends_at = _number(sub(status, "job", "ends_at"))
    if ends_at is None:
        return None
    try:
        return dt_util.utc_from_timestamp(ends_at)
    except (OverflowError, OSError, ValueError):
        return None


def _target_temperature(status: dict[str, Any]) -> int | float | None:
    target = _number(status.get("target_temperature"))
    if target is None:
        target = _number(sub(status, "job", "target_temperature"))
    return target


def _job_attributes(status: dict[str, Any]) -> dict[str, Any]:
    job = status.get("job")
    if not isinstance(job, dict):
        return {}
    return {
        "job_state": job.get("state"),
        "pause_reason": job.get("pause_reason"),
        "total": job.get("total"),
        "phase": job.get("phase"),
        "phases": job.get("phases"),
        "reverse": job.get("reverse"),
        "accessory": job.get("accessory"),
    }


def _guided_step_attributes(status: dict[str, Any]) -> dict[str, Any]:
    guided = status.get("guided")
    if not isinstance(guided, dict):
        return {}
    return {
        # The state is cut at 255 characters; this is the whole text.
        "full_text": guided.get("step_text"),
        "step_kind": guided.get("step_kind"),
        "weigh_target": guided.get("weigh_target"),
    }


SENSORS: tuple[ToqueSensorDescription, ...] = (
    ToqueSensorDescription(
        key="state",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=list(ROBOT_STATES),
        value_fn=lambda s: s.get("state"),
        attributes_fn=lambda s: {"mode": s.get("mode")},
    ),
    ToqueSensorDescription(
        key="job_kind",
        translation_key="job_kind",
        device_class=SensorDeviceClass.ENUM,
        options=list(JOB_KINDS),
        value_fn=lambda s: sub(s, "job", "kind"),
        attributes_fn=_job_attributes,
    ),
    ToqueSensorDescription(
        key="job_label",
        translation_key="job_label",
        value_fn=lambda s: short_text(sub(s, "job", "label")),
    ),
    ToqueSensorDescription(
        key="time_remaining",
        translation_key="time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda s: _number(sub(s, "job", "remaining")),
    ),
    ToqueSensorDescription(
        key="end_time",
        translation_key="end_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_end_time,
    ),
    ToqueSensorDescription(
        key="bowl_temperature",
        translation_key="bowl_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _number(s.get("temperature")),
        attributes_fn=lambda s: {"below_range": bool(s.get("temperature_below_range"))},
    ),
    ToqueSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=_target_temperature,
    ),
    ToqueSensorDescription(
        key="speed",
        translation_key="speed",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _number(s.get("speed")),
    ),
    ToqueSensorDescription(
        key="rpm",
        translation_key="rpm",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda s: _number(s.get("rpm")),
    ),
    ToqueSensorDescription(
        key="weight",
        translation_key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: _number(s.get("weight")),
        attributes_fn=lambda s: {"stable": s.get("weight_stable")},
    ),
    ToqueSensorDescription(
        key="guided_recipe",
        translation_key="guided_recipe",
        value_fn=lambda s: short_text(sub(s, "guided", "recipe_title")),
        attributes_fn=lambda s: {"recipe_id": sub(s, "guided", "recipe_id")},
    ),
    ToqueSensorDescription(
        key="guided_step",
        translation_key="guided_step",
        value_fn=lambda s: _number(sub(s, "guided", "step")),
    ),
    ToqueSensorDescription(
        key="guided_steps",
        translation_key="guided_steps",
        value_fn=lambda s: _number(sub(s, "guided", "steps")),
    ),
    ToqueSensorDescription(
        key="guided_step_text",
        translation_key="guided_step_text",
        value_fn=lambda s: short_text(sub(s, "guided", "step_text")),
        attributes_fn=_guided_step_attributes,
    ),
    ToqueSensorDescription(
        key="guided_step_setting",
        translation_key="guided_step_setting",
        value_fn=lambda s: short_text(sub(s, "guided", "step_setting")),
    ),
    ToqueSensorDescription(
        key="link",
        translation_key="link",
        device_class=SensorDeviceClass.ENUM,
        options=list(LINK_STATES),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.get("link"),
    ),
    ToqueSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: None if s.get("firmware") is None else str(s["firmware"]),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToqueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the sensors of one robot."""
    coordinator = entry.runtime_data
    async_add_entities(ToqueSensor(coordinator, description) for description in SENSORS)


class ToqueSensor(ToqueEntity, SensorEntity):
    """One value of the robot's status."""

    entity_description: ToqueSensorDescription

    def __init__(self, coordinator: ToqueCoordinator, description: ToqueSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """The value, or None (unknown) when the robot reports null."""
        status = self.coordinator.data
        if status is None:
            return None
        value = self.entity_description.value_fn(status)
        options = self.entity_description.options
        if options is not None and value not in options:
            # A value newer than this integration: unknown beats an error.
            return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra details that go with the value."""
        status = self.coordinator.data
        if status is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(status)
