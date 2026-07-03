"""Drives Home Assistant lights from Stage Kit events.

Design notes
------------
* The game emits Stage Kit commands very frequently mid-song. Consumer smart
  lights (especially Hue over Zigbee) cannot keep up with every packet, so
  updates per entity are coalesced and rate-limited: we always apply the
  *latest* desired state, at most once per ``min_update_interval_ms``.
* Each of the four Stage Kit LED rings (red / green / blue / yellow) can be
  mapped to one or more RGB light entities. If the same entity is mapped to
  several colors, the active colors are blended additively, weighted by how
  many of that ring's 8 LEDs are lit.
* Strobe is approximated by toggling the strobe entities in a background
  loop. Real Stage Kit strobe rates are faster than most smart bulbs can
  physically manage; WLED handles it best.
* Fog maps to any switch/light/fan entity (e.g. a smart plug on a fog
  machine) via homeassistant.turn_on / turn_off.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_MAX_BRIGHTNESS,
    DEFAULT_MIN_INTERVAL_MS,
    OPT_FOG_ENTITIES,
    OPT_LIGHTS_BLUE,
    OPT_LIGHTS_GREEN,
    OPT_LIGHTS_POSITIONS,
    OPT_LIGHTS_RED,
    OPT_LIGHTS_STROBE,
    OPT_LIGHTS_YELLOW,
    OPT_MAX_BRIGHTNESS,
    OPT_MIN_INTERVAL_MS,
    OPT_TURN_OFF_IN_MENUS,
)
from .protocol import LED_COLOR_RGB

_LOGGER = logging.getLogger(__name__)

# Approximate strobe half-periods (seconds between on/off flips).
# The physical pod strobes faster, but network lights can't.
STROBE_HALF_PERIOD = {
    "slow": 0.50,
    "medium": 0.33,
    "fast": 0.22,
    "fastest": 0.15,
}

COLOR_OPTS = {
    "red": OPT_LIGHTS_RED,
    "green": OPT_LIGHTS_GREEN,
    "blue": OPT_LIGHTS_BLUE,
    "yellow": OPT_LIGHTS_YELLOW,
}


class StageKitLightDriver:
    """Translates decoded Stage Kit events into light service calls."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self._color_entities: dict[str, list[str]] = {
            color: list(options.get(opt) or []) for color, opt in COLOR_OPTS.items()
        }
        self._strobe_entities: list[str] = list(options.get(OPT_LIGHTS_STROBE) or [])
        self._fog_entities: list[str] = list(options.get(OPT_FOG_ENTITIES) or [])
        self._max_brightness: int = int(
            options.get(OPT_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS)
        )
        self._min_interval: float = (
            int(options.get(OPT_MIN_INTERVAL_MS, DEFAULT_MIN_INTERVAL_MS)) / 1000.0
        )
        self._turn_off_in_menus: bool = bool(options.get(OPT_TURN_OFF_IN_MENUS, True))

        # Positional / per-LED lights: the 8 LED slots are spread across
        # however many entities are mapped, and each shows the per-position
        # blend of the four colors.
        position_entities: list[str] = list(options.get(OPT_LIGHTS_POSITIONS) or [])
        self._position_slots: dict[str, list[int]] = {}
        n_pos = len(position_entities)
        for idx, entity_id in enumerate(position_entities):
            self._position_slots[entity_id] = self._slots_for(idx, n_pos)

        # Current 8-bit lit-LED mask per color ring (left channel of the cue).
        self._masks: dict[str, int] = {c: 0 for c in COLOR_OPTS}
        # per-entity throttle bookkeeping
        self._last_sent: dict[str, float] = {}
        self._last_state: dict[str, tuple] = {}
        self._pending: dict[str, asyncio.TimerHandle] = {}
        self._strobe_task: asyncio.Task | None = None
        self._fog_on = False

    @staticmethod
    def _slots_for(idx: int, n: int) -> list[int]:
        """Assign the idx-th of n entities its contiguous share of 8 LED slots."""
        if n <= 0:
            return []
        base, rem = divmod(8, n)
        start = 0
        for i in range(n):
            size = base + (1 if i < rem else 0)
            if i == idx:
                return list(range(start, start + size))
            start += size
        return []

    def _count(self, color: str) -> int:
        """Number of lit LEDs in a color ring."""
        return bin(self._masks[color]).count("1")

    # ------------------------------------------------------------------ API

    def summary(self) -> str:
        """Human-readable mapping summary for logs."""
        mapped = {c: e for c, e in self._color_entities.items() if e}
        return (
            f"colors={mapped or 'none'}, "
            f"positions={list(self._position_slots) or 'none'}, "
            f"strobe={self._strobe_entities or 'none'}, "
            f"fog={self._fog_entities or 'none'}, "
            f"min_interval={self._min_interval * 1000:.0f}ms"
        )

    def handle_stagekit(self, data: dict[str, Any]) -> None:
        """Handle a decoded stagekit event dict from protocol.decode_stagekit."""
        command = data.get("command")
        _LOGGER.debug("Stage Kit cue: %s", data)
        if command == "led":
            self._masks[data["color"]] = data["mask"]
            self._refresh_entities()
        elif command == "strobe":
            self._set_strobe(data.get("speed", "off"))
        elif command == "fog":
            self._set_fog(bool(data.get("on")))
        elif command == "disable_all":
            self.all_off()

    def handle_game_state(self, in_game: bool) -> None:
        """Optionally black out when returning to the menus."""
        if not in_game and self._turn_off_in_menus:
            self.all_off()

    def all_off(self) -> None:
        """Turn everything managed by the driver off."""
        for color in self._masks:
            self._masks[color] = 0
        self._set_strobe("off")
        self._set_fog(False)
        self._refresh_entities(force_off=True)

    async def async_shutdown(self) -> None:
        """Cancel timers and background tasks."""
        for handle in self._pending.values():
            handle.cancel()
        self._pending.clear()
        if self._strobe_task:
            self._strobe_task.cancel()
            self._strobe_task = None

    # ------------------------------------------------------------- internals

    def _all_color_entities(self) -> set[str]:
        out: set[str] = set()
        for ents in self._color_entities.values():
            out.update(ents)
        return out

    def _desired_state(self, entity_id: str) -> tuple:
        """Whole-ring light: blend every color this entity is mapped to.

        Brightness scales with the fullest ring the entity belongs to.
        """
        return self._blend(
            {
                color: (self._count(color), 8)
                for color, entities in self._color_entities.items()
                if entity_id in entities
            }
        )

    def _desired_state_position(self, entity_id: str) -> tuple:
        """Positional light: blend the four colors over its assigned LED slots."""
        slots = self._position_slots.get(entity_id, [])
        if not slots:
            return (False, None, None)
        contributions: dict[str, tuple[int, int]] = {}
        for color in COLOR_OPTS:
            mask = self._masks[color]
            lit = sum(1 for s in slots if mask & (1 << s))
            if lit:
                contributions[color] = (lit, len(slots))
        return self._blend(contributions)

    def _blend(self, contributions: dict[str, tuple[int, int]]) -> tuple:
        """Additively blend colors, each weighted by lit/total fraction.

        contributions: {color: (lit, total)}. Returns (on, rgb, brightness).
        """
        r = g = b = 0.0
        max_frac = 0.0
        for color, (lit, total) in contributions.items():
            if total <= 0 or lit <= 0:
                continue
            frac = lit / total
            max_frac = max(max_frac, frac)
            cr, cg, cb = LED_COLOR_RGB[color]
            r += cr * frac
            g += cg * frac
            b += cb * frac
        if max_frac <= 0:
            return (False, None, None)
        scale = max(r, g, b)
        rgb = (
            round(r / scale * 255),
            round(g / scale * 255),
            round(b / scale * 255),
        )
        brightness = max(1, round(self._max_brightness * max_frac))
        return (True, rgb, brightness)

    def _refresh_entities(self, force_off: bool = False) -> None:
        for entity_id in self._all_color_entities():
            state = (False, None, None) if force_off else self._desired_state(entity_id)
            self._schedule(entity_id, state)
        for entity_id in self._position_slots:
            state = (
                (False, None, None)
                if force_off
                else self._desired_state_position(entity_id)
            )
            self._schedule(entity_id, state)

    def _schedule(self, entity_id: str, state: tuple) -> None:
        """Rate-limited, latest-wins application of a desired state."""
        if self._last_state.get(entity_id) == state:
            return
        self._last_state[entity_id] = state
        now = time.monotonic()
        wait = self._last_sent.get(entity_id, 0) + self._min_interval - now
        if entity_id in self._pending:
            return  # a pending flush will pick up the latest state
        if wait <= 0:
            self._flush(entity_id)
        else:
            loop = self.hass.loop
            self._pending[entity_id] = loop.call_later(
                wait, self._flush_pending, entity_id
            )

    def _flush_pending(self, entity_id: str) -> None:
        self._pending.pop(entity_id, None)
        self._flush(entity_id)

    def _flush(self, entity_id: str) -> None:
        self._last_sent[entity_id] = time.monotonic()
        on, rgb, brightness = self._last_state.get(entity_id, (False, None, None))
        if on:
            # transition=0 stops bulbs (esp. Hue) applying a default fade,
            # which otherwise makes fast cues look like nothing is happening.
            data = {"entity_id": entity_id, "rgb_color": list(rgb), "transition": 0}
            if brightness:
                data["brightness"] = brightness
            _LOGGER.debug("light.turn_on %s rgb=%s bri=%s", entity_id, rgb, brightness)
            self.hass.async_create_task(
                self.hass.services.async_call("light", "turn_on", data, blocking=False)
            )
        else:
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "light", "turn_off", {"entity_id": entity_id}, blocking=False
                )
            )

    # -------------------------------------------------------------- strobe

    def _set_strobe(self, speed: str) -> None:
        if self._strobe_task:
            self._strobe_task.cancel()
            self._strobe_task = None
        if speed == "off" or speed not in STROBE_HALF_PERIOD:
            self._strobe_off()
            return
        if not self._strobe_entities:
            return
        self._strobe_task = self.hass.loop.create_task(
            self._strobe_loop(STROBE_HALF_PERIOD[speed])
        )

    async def _strobe_loop(self, half_period: float) -> None:
        on = True
        try:
            while True:
                service = "turn_on" if on else "turn_off"
                data: dict[str, Any] = {"entity_id": self._strobe_entities}
                if on:
                    data["brightness"] = self._max_brightness
                await self.hass.services.async_call(
                    "light", service, data, blocking=False
                )
                on = not on
                await asyncio.sleep(half_period)
        except asyncio.CancelledError:
            pass

    def _strobe_off(self) -> None:
        if self._strobe_entities:
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "light",
                    "turn_off",
                    {"entity_id": self._strobe_entities},
                    blocking=False,
                )
            )

    # ----------------------------------------------------------------- fog

    def _set_fog(self, on: bool) -> None:
        if not self._fog_entities or on == self._fog_on:
            return
        self._fog_on = on
        self.hass.async_create_task(
            self.hass.services.async_call(
                "homeassistant",
                "turn_on" if on else "turn_off",
                {"entity_id": self._fog_entities},
                blocking=False,
            )
        )
