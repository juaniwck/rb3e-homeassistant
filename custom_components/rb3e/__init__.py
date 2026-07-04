"""RB3Enhanced (Rock Band 3) integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
    HA_EVENT_TYPE,
    OPT_FIRE_EVENTS,
    OPT_LIGHT_DRIVER,
    OPT_WLED_ENABLED,
    SIGNAL_UPDATE,
)
from .light_driver import StageKitLightDriver
from .wled_driver import WledStageKitDriver
from .protocol import (
    EVENT_ALIVE,
    EVENT_BAND_INFO,
    EVENT_DX_DATA,
    EVENT_SCORE,
    EVENT_SCREEN_NAME,
    EVENT_SONG_ARTIST,
    EVENT_SONG_NAME,
    EVENT_SONG_SHORTNAME,
    EVENT_STAGEKIT,
    EVENT_STATE,
    EVENT_VENUE_NAME,
    Rb3eEvent,
    parse_packet,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

type Rb3eConfigEntry = ConfigEntry[Rb3eHub]


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, hub: Rb3eHub) -> None:
        self.hub = hub

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.hub.handle_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error: %s", exc)


class Rb3eHub:
    """Owns the UDP socket, decoded game state, and the light driver."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.signal = f"{SIGNAL_UPDATE}_{entry.entry_id}"
        self.transport: asyncio.DatagramTransport | None = None
        self.light_driver: StageKitLightDriver | None = None
        self.wled_driver: WledStageKitDriver | None = None
        self.last_packet: float | None = None  # time.monotonic()
        self.source: str | None = None
        self._last_sk_dispatch: float = 0.0  # throttle stagekit entity updates

        # Latest known game state, read by the entities.
        self.state: dict[str, Any] = {
            "game_state": None,  # "menus" | "in_game"
            "in_game": False,
            "song_name": None,
            "song_artist": None,
            "song_shortname": None,
            "venue": None,
            "screen": None,
            "band_members": None,
            "platform": None,
            "build_tag": None,
            "stagekit": None,  # last decoded stagekit dict
            "stagekit_count": 0,  # total stagekit packets seen this session
        }

    # ----------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        port = self.entry.data.get(CONF_PORT, DEFAULT_PORT)
        loop = self.hass.loop
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self),
            local_addr=("0.0.0.0", port),
            allow_broadcast=True,
        )
        self._apply_options()
        if self.wled_driver:
            await self.wled_driver.async_start()
        _LOGGER.info("RB3Enhanced listener started on UDP port %s", port)

    async def async_stop(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None
        if self.light_driver:
            await self.light_driver.async_shutdown()
            self.light_driver = None
        if self.wled_driver:
            await self.wled_driver.async_shutdown()
            self.wled_driver = None

    def _apply_options(self) -> None:
        options = self.entry.options
        if options.get(OPT_LIGHT_DRIVER):
            self.light_driver = StageKitLightDriver(self.hass, dict(options))
            _LOGGER.debug(
                "Stage Kit light driver enabled: %s", self.light_driver.summary()
            )
        else:
            self.light_driver = None
            _LOGGER.debug("Stage Kit light driver disabled in options")
        if options.get(OPT_WLED_ENABLED):
            self.wled_driver = WledStageKitDriver(self.hass, dict(options))
            _LOGGER.debug("WLED Stage Kit driver enabled: %s", self.wled_driver.summary())
        else:
            self.wled_driver = None
            _LOGGER.debug("WLED Stage Kit driver disabled in options")

    # ------------------------------------------------------------ handling

    @callback
    def handle_datagram(self, data: bytes, addr) -> None:  # noqa: ANN001
        event = parse_packet(data)
        if event is None:
            return
        self.last_packet = time.monotonic()
        self.source = addr[0] if addr else None
        self.state["platform"] = event.platform

        changed = self._update_state(event)

        # Fire on the HA event bus for automations.
        if self.entry.options.get(OPT_FIRE_EVENTS, True):
            self.hass.bus.async_fire(
                HA_EVENT_TYPE,
                {
                    "type": event.type_name,
                    "platform": event.platform,
                    "source": self.source,
                    **event.data,
                },
            )

        # Feed the light engines: entity-based (slow lights, e.g. Hue) and
        # WLED direct UDP (fast, per-LED). Both can run at once.
        for driver in (self.light_driver, self.wled_driver):
            if not driver:
                continue
            if event.type == EVENT_STAGEKIT:
                driver.handle_stagekit(event.data)
            elif event.type == EVENT_STATE:
                driver.handle_game_state(event.data["in_game"])

        # Push the diagnostic stagekit sensor at most twice a second so the
        # user can confirm cues are arriving without flooding the state machine.
        if event.type == EVENT_STAGEKIT:
            now = time.monotonic()
            if now - self._last_sk_dispatch >= 0.5:
                self._last_sk_dispatch = now
                changed = True

        if changed:
            async_dispatcher_send(self.hass, self.signal)

    def _update_state(self, event: Rb3eEvent) -> bool:
        """Fold an event into the state dict. Returns True if entities changed."""
        s = self.state
        if event.type == EVENT_ALIVE:
            s["build_tag"] = event.data.get("build_tag")
            return True
        if event.type == EVENT_STATE:
            s["game_state"] = event.data["state"]
            s["in_game"] = event.data["in_game"]
            if not event.data["in_game"]:
                # Back at the menus: there is no current song.
                s["song_name"] = None
                s["song_artist"] = None
                s["song_shortname"] = None
            return True
        if event.type == EVENT_SONG_NAME:
            s["song_name"] = event.data["value"] or None
            return True
        if event.type == EVENT_SONG_ARTIST:
            s["song_artist"] = event.data["value"] or None
            return True
        if event.type == EVENT_SONG_SHORTNAME:
            s["song_shortname"] = event.data["value"] or None
            return True
        if event.type == EVENT_VENUE_NAME:
            s["venue"] = event.data["value"] or None
            return True
        if event.type == EVENT_SCREEN_NAME:
            s["screen"] = event.data["value"] or None
            return True
        if event.type == EVENT_SCORE:
            # Note: mainline RB3Enhanced does not emit SCORE packets. If a
            # fork does, this keeps the data available on the event bus.
            return False
        if event.type == EVENT_BAND_INFO:
            s["band_members"] = event.data.get("members")
            return True
        if event.type == EVENT_STAGEKIT:
            s["stagekit"] = event.data
            s["stagekit_count"] += 1
            # Handled with its own throttle in handle_datagram (too chatty
            # to push to entities on every packet).
            return False
        if event.type == EVENT_DX_DATA:
            return False
        return False


async def async_setup_entry(hass: HomeAssistant, entry: Rb3eConfigEntry) -> bool:
    """Set up RB3Enhanced from a config entry."""
    hub = Rb3eHub(hass, entry)
    try:
        await hub.async_start()
    except OSError as err:
        from homeassistant.exceptions import ConfigEntryNotReady

        raise ConfigEntryNotReady(f"Could not bind UDP port: {err}") from err

    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: Rb3eConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok
