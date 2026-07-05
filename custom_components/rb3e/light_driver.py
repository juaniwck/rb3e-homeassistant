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
    OPT_LIGHTS_RED,
    OPT_LIGHTS_STROBE,
    OPT_LIGHTS_YELLOW,
    OPT_MAX_BRIGHTNESS,
    OPT_MIN_INTERVAL_MS,
    OPT_OFF_ON_SCORE_SCREENS,
    OPT_TURN_OFF_IN_MENUS,
    OPT_ZONES_BLUE,
    OPT_ZONES_GREEN,
    OPT_ZONES_RED,
    OPT_ZONES_YELLOW,
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

# After an all-off, re-send turn_off at these offsets (seconds) to catch
# turn_on commands that were already queued in a slow bridge (Hue).
OFF_REASSERT_DELAYS = (1.0, 3.0)

# When a song finishes, RB3 keeps game_state == in_game through the score
# screens and runs its own ~17s Stage Kit light show there. Every score
# screen name contains this hint (endgame_waiting_screen, coop_endgame_screen,
# endgame_advance_screen, ...); actual gameplay is game_screen.
SCORE_SCREEN_HINT = "endgame"
GAMEPLAY_SCREEN = "game_screen"

COLOR_OPTS = {
    "red": OPT_LIGHTS_RED,
    "green": OPT_LIGHTS_GREEN,
    "blue": OPT_LIGHTS_BLUE,
    "yellow": OPT_LIGHTS_YELLOW,
}

# Ordered per-color zone lists, ESP32-SK style: the ring's 8 LEDs are
# distributed across however many entities are mapped, in list order.
ZONE_OPTS = {
    "red": OPT_ZONES_RED,
    "green": OPT_ZONES_GREEN,
    "blue": OPT_ZONES_BLUE,
    "yellow": OPT_ZONES_YELLOW,
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
        self._off_on_score: bool = bool(
            options.get(OPT_OFF_ON_SCORE_SCREENS, True)
        )

        # Per-color zones: for each color ring, an ordered entity list. Each
        # entity gets a contiguous share of that ring's 8 LEDs (map 8 entities
        # -> one LED each; 1 entity -> the whole ring; 3 -> 3/3/2; etc).
        # _zone_slots: entity_id -> {color: [led slot indices]}
        self._zone_slots: dict[str, dict[str, list[int]]] = {}
        for color, opt in ZONE_OPTS.items():
            entities = list(options.get(opt) or [])
            n = len(entities)
            for idx, entity_id in enumerate(entities):
                self._zone_slots.setdefault(entity_id, {})[color] = self._slots_for(
                    idx, n
                )

        # Current 8-bit lit-LED mask per color ring (left channel of the cue).
        self._masks: dict[str, int] = {c: 0 for c in COLOR_OPTS}
        # per-entity throttle bookkeeping
        self._last_sent: dict[str, float] = {}
        self._last_state: dict[str, tuple] = {}
        self._pending: dict[str, asyncio.TimerHandle] = {}
        self._strobe_task: asyncio.Task | None = None
        self._fog_on = False
        # True until a game_state packet says otherwise, so cues still work
        # if HA (re)starts mid-song.
        self._in_game = True
        # True while on an endgame/score screen (see SCORE_SCREEN_HINT).
        self._on_score_screen = False
        self._off_reasserts: list[asyncio.TimerHandle] = []

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
            f"zones={list(self._zone_slots) or 'none'}, "
            f"strobe={self._strobe_entities or 'none'}, "
            f"fog={self._fog_entities or 'none'}, "
            f"min_interval={self._min_interval * 1000:.0f}ms"
        )

    def handle_stagekit(self, data: dict[str, Any]) -> None:
        """Handle a decoded stagekit event dict from protocol.decode_stagekit."""
        command = data.get("command")
        _LOGGER.debug("Stage Kit cue: %s", data)
        if self._cues_blocked():
            # Dark at the menus and on the score screens: ignore anything
            # that would relight the lights (late packets, the game's
            # endgame light show) so automations own them until the next
            # song. Fog-off is still honored so a fog machine is never
            # left running.
            if command == "fog":
                self._set_fog(False)
            elif command == "disable_all":
                self.all_off()
            return
        if command in ("led", "strobe"):
            self._cancel_off_reasserts()
        if command == "led":
            self._masks[data["color"]] = data["mask"]
            self._refresh_entities()
        elif command == "strobe":
            self._set_strobe(data.get("speed", "off"))
        elif command == "fog":
            self._set_fog(bool(data.get("on")))
        elif command == "disable_all":
            self.all_off()

    def _cues_blocked(self) -> bool:
        """True when incoming light cues should be ignored."""
        if self._turn_off_in_menus and not self._in_game:
            return True
        return self._off_on_score and self._on_score_screen

    def handle_game_state(self, in_game: bool) -> None:
        """Track game state; optionally black out at the menus."""
        self._in_game = in_game
        if in_game:
            # A new song is starting; any previous score screen is over.
            self._on_score_screen = False
        elif self._turn_off_in_menus:
            self.all_off()

    def handle_screen(self, name: str) -> None:
        """Track the shell screen; black out on the endgame/score screens.

        The game keeps game_state == in_game during the score screens and
        runs its own Stage Kit light show there, so screen names are the
        only reliable end-of-song signal.
        """
        name = (name or "").lower()
        if SCORE_SCREEN_HINT in name:
            if not self._on_score_screen:
                self._on_score_screen = True
                if self._off_on_score:
                    _LOGGER.debug("Score screen '%s': blacking out", name)
                    self.all_off()
        elif name == GAMEPLAY_SCREEN:
            self._on_score_screen = False

    def all_off(self) -> None:
        """Turn everything managed by the driver off, immediately.

        An off must never wait behind the rate limiter: pending flush
        timers are cancelled and turn_off is sent right away. Because slow
        bridges (Hue) can still apply turn_on commands that were already in
        flight *after* our off lands, the off is re-asserted a couple of
        times shortly afterwards.
        """
        for color in self._masks:
            self._masks[color] = 0
        self._set_strobe("off")
        self._set_fog(False)
        for handle in self._pending.values():
            handle.cancel()
        self._pending.clear()
        for entity_id in self._all_color_entities() | set(self._zone_slots):
            self._last_state[entity_id] = (False, None, None)
            self._flush(entity_id)
        self._schedule_off_reasserts()

    def _schedule_off_reasserts(self) -> None:
        self._cancel_off_reasserts()
        loop = self.hass.loop
        self._off_reasserts = [
            loop.call_later(delay, self._reassert_off)
            for delay in OFF_REASSERT_DELAYS
        ]

    def _cancel_off_reasserts(self) -> None:
        for handle in self._off_reasserts:
            handle.cancel()
        self._off_reasserts = []

    def _reassert_off(self) -> None:
        """Re-send off to sweep up in-flight turn_ons on slow bridges."""
        if any(self._masks.values()) or self._strobe_task:
            return  # something relit the lights on purpose; leave them be
        entities = sorted(
            self._all_color_entities()
            | set(self._zone_slots)
            | set(self._strobe_entities)
        )
        if not entities:
            return
        self.hass.async_create_task(
            self.hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": entities, "transition": 0},
                blocking=False,
            )
        )

    async def async_shutdown(self) -> None:
        """Cancel timers and background tasks."""
        for handle in self._pending.values():
            handle.cancel()
        self._pending.clear()
        self._cancel_off_reasserts()
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
        """Compute (on, rgb, brightness) for an entity.

        Whole-ring ("mixing") mappings contribute (lit LEDs in ring, 8) per
        color; zone mappings contribute (lit LEDs in this entity's slots,
        slot count) and take precedence for their color if an entity is
        somehow in both lists for the same color. All active contributions
        are blended additively.
        """
        contributions: dict[str, tuple[int, int]] = {}
        for color, entities in self._color_entities.items():
            if entity_id in entities:
                contributions[color] = (self._count(color), 8)
        for color, slots in self._zone_slots.get(entity_id, {}).items():
            if not slots:
                continue
            mask = self._masks[color]
            lit = sum(1 for s in slots if mask & (1 << s))
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
        for entity_id in self._all_color_entities() | set(self._zone_slots):
            state = (False, None, None) if force_off else self._desired_state(entity_id)
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
            # transition=0: shut off instantly instead of the bulb's default
            # fade; fading back up is left to user automations.
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "light",
                    "turn_off",
                    {"entity_id": entity_id, "transition": 0},
                    blocking=False,
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
