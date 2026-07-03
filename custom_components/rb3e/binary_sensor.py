"""Binary sensors for the RB3Enhanced integration."""

from __future__ import annotations

import time
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import Rb3eConfigEntry, Rb3eHub
from .const import CONNECTION_TIMEOUT_S, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Rb3eConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities([Rb3eInSongSensor(hub, entry), Rb3eConnectedSensor(hub, entry)])


class Rb3eBaseBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: Rb3eHub, entry: Rb3eConfigEntry, key: str) -> None:
        self.hub = hub
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
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


class Rb3eInSongSensor(Rb3eBaseBinarySensor):
    """On while the game is in a song."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, hub: Rb3eHub, entry: Rb3eConfigEntry) -> None:
        super().__init__(hub, entry, "in_song")

    @property
    def is_on(self) -> bool:
        return bool(self.hub.state["in_game"])


class Rb3eConnectedSensor(Rb3eBaseBinarySensor):
    """On while packets have been received recently."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = True

    def __init__(self, hub: Rb3eHub, entry: Rb3eConfigEntry) -> None:
        super().__init__(hub, entry, "connected")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Re-evaluate periodically so we can flip to off after a timeout.
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._periodic, timedelta(seconds=15)
            )
        )

    @callback
    def _periodic(self, _now) -> None:  # noqa: ANN001
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        last = self.hub.last_packet
        return last is not None and (time.monotonic() - last) < CONNECTION_TIMEOUT_S

    @property
    def extra_state_attributes(self) -> dict:
        return {"source_ip": self.hub.source}
