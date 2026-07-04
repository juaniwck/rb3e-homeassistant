"""Drives a WLED strip directly over UDP from Stage Kit events.

This is the "fast" engine, meant for addressable strips running WLED. It is
independent from :mod:`light_driver` (the Home Assistant entity engine, meant
for slower lights like Hue): both can run at the same time.

Design notes
------------
* Frames are pushed with WLED's UDP realtime protocol (DNRGB, protocol 4,
  default port 21324). This bypasses HA's entity/service machinery entirely,
  so per-LED updates at 50 fps are no problem.
* Every packet carries a timeout byte (seconds). When we stop sending (song
  over, menus, disable_all) we push one black frame with a 1 second timeout
  and go quiet — WLED then drops out of realtime mode and hands the strip
  back to whatever normally owns it (its own effects, or Hyperion). This is
  what makes Rock Band + Hyperion coexist on one strip with zero switching
  logic.
* The Stage Kit state is 4 color rings x 8 LED positions. Mapping onto a
  strip of N LEDs:
    - ``blend``      the strip is split into 8 position segments; each
                     segment shows the additive blend of every color whose
                     bit is lit at that position.
    - ``interleave`` each position segment is further split into 4 color
                     lanes (R/G/B/Y repeating), so simultaneous colors render
                     side by side like the physical pod.
    - custom map     a JSON object giving explicit strip indices per color
                     and position (ESP32-SK style), overriding the mode.
* Strobe runs at the real Stage Kit rates (~6/8/10/12 Hz) as a local task:
  white flash, then the current LED frame is restored between flashes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_WLED_LED_COUNT,
    DEFAULT_WLED_MIN_INTERVAL_MS,
    DEFAULT_WLED_PORT,
    DEFAULT_WLED_TIMEOUT_S,
    OPT_TURN_OFF_IN_MENUS,
    OPT_WLED_CUSTOM_MAP,
    OPT_WLED_HOST,
    OPT_WLED_LED_COUNT,
    OPT_WLED_LED_START,
    OPT_WLED_MAX_BRIGHTNESS,
    OPT_WLED_MIN_INTERVAL_MS,
    OPT_WLED_MODE,
    OPT_WLED_PORT,
    OPT_WLED_REVERSE,
    OPT_WLED_STROBE_ENABLED,
    OPT_WLED_TIMEOUT_S,
    WLED_MODE_BLEND,
    WLED_MODE_INTERLEAVE,
)
from .protocol import LED_COLOR_RGB

_LOGGER = logging.getLogger(__name__)

DNRGB = 4  # WLED UDP realtime protocol id (16-bit start index, RGB data)
DNRGB_MAX_LEDS = 489  # max LEDs per DNRGB packet
RELEASE_TIMEOUT_S = 1  # timeout byte on the final black frame

# Interleave lane order within each position segment.
LANE_ORDER = ("red", "green", "blue", "yellow")

# Full strobe periods in seconds, matching the ESP32-SK / StageKitPied
# defaults (~6 / 8 / 10 / 12 Hz). The flash itself is FLASH_S long.
STROBE_PERIOD = {
    "slow": 0.167,
    "medium": 0.125,
    "fast": 0.100,
    "fastest": 0.083,
}
FLASH_S = 0.030

STROBE_RGB = (255, 255, 255)


class _WledUdp(asyncio.DatagramProtocol):
    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("WLED UDP error: %s", exc)


class WledStageKitDriver:
    """Renders Stage Kit state into per-LED frames sent straight to WLED."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        self.hass = hass
        self._host: str = str(options.get(OPT_WLED_HOST) or "").strip()
        self._port: int = int(options.get(OPT_WLED_PORT, DEFAULT_WLED_PORT))
        self._count: int = int(
            options.get(OPT_WLED_LED_COUNT, DEFAULT_WLED_LED_COUNT)
        )
        self._start: int = int(options.get(OPT_WLED_LED_START, 0))
        self._reverse: bool = bool(options.get(OPT_WLED_REVERSE, False))
        self._mode: str = options.get(OPT_WLED_MODE, WLED_MODE_BLEND)
        self._max_brightness: int = int(options.get(OPT_WLED_MAX_BRIGHTNESS, 255))
        self._timeout_s: int = min(
            254, max(1, int(options.get(OPT_WLED_TIMEOUT_S, DEFAULT_WLED_TIMEOUT_S)))
        )
        self._min_interval: float = (
            int(options.get(OPT_WLED_MIN_INTERVAL_MS, DEFAULT_WLED_MIN_INTERVAL_MS))
            / 1000.0
        )
        self._strobe_enabled: bool = bool(options.get(OPT_WLED_STROBE_ENABLED, True))
        self._turn_off_in_menus: bool = bool(options.get(OPT_TURN_OFF_IN_MENUS, True))

        # color -> position (0-7) -> list of strip indices (0-based, relative
        # to the whole WLED strip). Built once from the custom map or mode.
        self._map: dict[str, dict[int, list[int]]] = {}
        self._strobe_leds: list[int] | None = None  # None = whole range
        self._build_map(options.get(OPT_WLED_CUSTOM_MAP) or "")

        self._masks: dict[str, int] = {c: 0 for c in LED_COLOR_RGB}
        self._transport: asyncio.DatagramTransport | None = None
        self._last_sent: float = 0.0
        self._pending: asyncio.TimerHandle | None = None
        self._dirty = False
        self._strobe_task: asyncio.Task | None = None
        self._active = False  # True while we own the strip (frames sent)

    # ------------------------------------------------------------ mapping

    def _range(self) -> range:
        return range(self._start, self._start + self._count)

    def _build_map(self, custom_json: str) -> None:
        custom = None
        if custom_json.strip():
            try:
                custom = json.loads(custom_json)
            except ValueError as err:
                _LOGGER.error(
                    "Invalid WLED custom map JSON (%s); falling back to '%s' mode",
                    err,
                    self._mode,
                )
        if isinstance(custom, dict):
            for color in LED_COLOR_RGB:
                positions = custom.get(color) or {}
                self._map[color] = {
                    int(pos): [int(i) for i in leds]
                    for pos, leds in positions.items()
                    if 0 <= int(pos) <= 7
                }
            strobe = custom.get("strobe")
            if isinstance(strobe, list):
                self._strobe_leds = [int(i) for i in strobe]
            return

        # Segment boundaries for the 8 Stage Kit positions across the range.
        strip = list(self._range())
        if self._reverse:
            strip.reverse()
        n = len(strip)
        segments = [strip[p * n // 8 : (p + 1) * n // 8] for p in range(8)]

        if self._mode == WLED_MODE_INTERLEAVE:
            for lane, color in enumerate(LANE_ORDER):
                self._map[color] = {
                    p: [led for i, led in enumerate(seg) if i % 4 == lane]
                    for p, seg in enumerate(segments)
                }
        else:  # blend
            for color in LED_COLOR_RGB:
                self._map[color] = {p: list(seg) for p, seg in enumerate(segments)}

    # ---------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        """Open the UDP transport (resolves the host)."""
        if not self._host:
            _LOGGER.warning("WLED driver enabled but no host configured")
            return
        loop = self.hass.loop
        self._transport, _ = await loop.create_datagram_endpoint(
            _WledUdp, remote_addr=(self._host, self._port)
        )
        _LOGGER.info(
            "WLED Stage Kit driver started: %s:%s, %s LEDs from %s, mode=%s",
            self._host,
            self._port,
            self._count,
            self._start,
            "custom" if self._strobe_leds is not None or self._is_custom() else self._mode,
        )

    def _is_custom(self) -> bool:
        # Heuristic only used for the startup log line.
        blend_like = all(
            self._map.get(c) == self._map.get("red") for c in LED_COLOR_RGB
        )
        return not blend_like and self._mode != WLED_MODE_INTERLEAVE

    async def async_shutdown(self) -> None:
        if self._pending:
            self._pending.cancel()
            self._pending = None
        if self._strobe_task:
            self._strobe_task.cancel()
            self._strobe_task = None
        if self._transport:
            if self._active:
                self._send_frame(self._black(), timeout=RELEASE_TIMEOUT_S)
            self._transport.close()
            self._transport = None

    def summary(self) -> str:
        return (
            f"host={self._host}:{self._port}, leds={self._count}@{self._start}, "
            f"mode={self._mode}, timeout={self._timeout_s}s, "
            f"min_interval={self._min_interval * 1000:.0f}ms"
        )

    # -------------------------------------------------------------- events

    def handle_stagekit(self, data: dict[str, Any]) -> None:
        command = data.get("command")
        if command == "led":
            self._masks[data["color"]] = data["mask"]
            self._mark_dirty()
        elif command == "strobe":
            self._set_strobe(data.get("speed", "off"))
        elif command == "disable_all":
            self.all_off()
        # fog is intentionally not handled here; use the entity engine's
        # fog mapping for that.

    def handle_game_state(self, in_game: bool) -> None:
        if not in_game and self._turn_off_in_menus:
            self.all_off()

    def all_off(self) -> None:
        """Black out and release the strip back to WLED/Hyperion."""
        for color in self._masks:
            self._masks[color] = 0
        if self._strobe_task:
            self._strobe_task.cancel()
            self._strobe_task = None
        if self._pending:
            self._pending.cancel()
            self._pending = None
        if self._transport and self._active:
            # Short timeout: WLED exits realtime mode ~1s later and hands
            # control back to its own effects / Hyperion.
            self._send_frame(self._black(), timeout=RELEASE_TIMEOUT_S)
        self._active = False

    # ------------------------------------------------------------- frames

    def _black(self) -> bytearray:
        return bytearray(self._count * 3)

    def _render(self) -> bytearray:
        """Render the current masks into an RGB frame for the LED range."""
        frame = self._black()
        scale = self._max_brightness / 255.0
        for color, positions in self._map.items():
            mask = self._masks.get(color, 0)
            if not mask:
                continue
            cr, cg, cb = LED_COLOR_RGB[color]
            cr, cg, cb = round(cr * scale), round(cg * scale), round(cb * scale)
            for pos in range(8):
                if not mask & (1 << pos):
                    continue
                for led in positions.get(pos, ()):
                    idx = (led - self._start) * 3
                    if 0 <= idx < len(frame) - 2:
                        frame[idx] = min(255, frame[idx] + cr)
                        frame[idx + 1] = min(255, frame[idx + 1] + cg)
                        frame[idx + 2] = min(255, frame[idx + 2] + cb)
        return frame

    def _white(self) -> bytearray:
        frame = self._render() if self._strobe_leds is not None else self._black()
        r, g, b = (round(c * self._max_brightness / 255.0) for c in STROBE_RGB)
        leds = self._strobe_leds if self._strobe_leds is not None else self._range()
        for led in leds:
            idx = (led - self._start) * 3
            if 0 <= idx < len(frame) - 2:
                frame[idx], frame[idx + 1], frame[idx + 2] = r, g, b
        return frame

    def _send_frame(self, frame: bytearray, timeout: int | None = None) -> None:
        """Chunk a frame into DNRGB packets and send them."""
        if not self._transport:
            return
        t = self._timeout_s if timeout is None else timeout
        led = 0
        total = len(frame) // 3
        while led < total:
            chunk = min(DNRGB_MAX_LEDS, total - led)
            start = self._start + led
            packet = bytes(
                (DNRGB, t, (start >> 8) & 0xFF, start & 0xFF)
            ) + bytes(frame[led * 3 : (led + chunk) * 3])
            self._transport.sendto(packet)
            led += chunk

    # ----------------------------------------------------- rate limiting

    def _mark_dirty(self) -> None:
        """Latest-wins, rate-limited frame push."""
        self._dirty = True
        if self._pending:
            return
        now = time.monotonic()
        wait = self._last_sent + self._min_interval - now
        if wait <= 0:
            self._flush()
        else:
            self._pending = self.hass.loop.call_later(wait, self._flush)

    def _flush(self) -> None:
        self._pending = None
        if not self._dirty:
            return
        self._dirty = False
        self._last_sent = time.monotonic()
        self._active = True
        self._send_frame(self._render())

    # -------------------------------------------------------------- strobe

    def _set_strobe(self, speed: str) -> None:
        if self._strobe_task:
            self._strobe_task.cancel()
            self._strobe_task = None
        if (
            not self._strobe_enabled
            or speed == "off"
            or speed not in STROBE_PERIOD
            or not self._transport
        ):
            if self._active:
                self._mark_dirty()  # repaint the base frame
            return
        self._strobe_task = self.hass.loop.create_task(
            self._strobe_loop(STROBE_PERIOD[speed])
        )

    async def _strobe_loop(self, period: float) -> None:
        try:
            while True:
                self._active = True
                self._send_frame(self._white())
                await asyncio.sleep(FLASH_S)
                self._send_frame(self._render())
                await asyncio.sleep(max(0.01, period - FLASH_S))
        except asyncio.CancelledError:
            pass
