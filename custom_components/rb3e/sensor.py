"""Sensors for the RB3Enhanced integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Rb3eConfigEntry, Rb3eHub
from .const import DOMAIN


@dataclass(frozen=True, kw_only=True)
class Rb3eSensorDescription(SensorEntityDescription):
    """Describes an RB3E sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSORS: tuple[Rb3eSensorDescription, ...] = (
    Rb3eSensorDescription(
        key="game_state",
        translation_key="game_state",
        device_class=SensorDeviceClass.ENUM,
        options=["menus", "in_game"],
        value_fn=lambda s: s["game_state"],
        attrs_fn=lambda s: {"screen": s["screen"]},
    ),
    Rb3eSensorDescription(
        key="song_name",
        translation_key="song_name",
        value_fn=lambda s: s["song_name"],
        attrs_fn=lambda s: {
            "artist": s["song_artist"],
            "shortname": s["song_shortname"],
            "venue": s["venue"],
        },
    ),
    Rb3eSensorDescription(
        key="song_artist",
        translation_key="song_artist",
        value_fn=lambda s: s["song_artist"],
    ),
    Rb3eSensorDescription(
        key="players",
        translation_key="players",
        state_class="measurement",
        value_fn=lambda s: (
            sum(1 for m in s["band_members"] if m["exists"])
            if s["band_members"]
            else None
        ),
        attrs_fn=lambda s: {"members": s["band_members"]},
    ),
    Rb3eSensorDescription(
        key="stagekit",
        translation_key="stagekit",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: (s["stagekit"] or {}).get("command"),
        attrs_fn=lambda s: {
            "last": s["stagekit"],
            "packets_received": s["stagekit_count"],
        },
    ),
    Rb3eSensorDescription(
        key="screen",
        translation_key="screen",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s["screen"],
    ),
    Rb3eSensorDescription(
        key="platform",
        translation_key="platform",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s["platform"],
        attrs_fn=lambda s: {"build_tag": s["build_tag"]},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Rb3eConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(Rb3eSensor(hub, entry, desc) for desc in SENSORS)


class Rb3eSensor(SensorEntity):
    """A push-updated sensor backed by the hub's state dict."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: Rb3eSensorDescription

    def __init__(
        self, hub: Rb3eHub, entry: Rb3eConfigEntry, description: Rb3eSensorDescription
    ) -> None:
        self.hub = hub
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Rock Band 3 Enhanced",
            manufacturer="RB3Enhanced",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.hub.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.hub.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.hub.state)
