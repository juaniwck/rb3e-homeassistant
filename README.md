# RB3Enhanced for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/juaniwck/rb3e-homeassistant/actions/workflows/validate.yml/badge.svg)](https://github.com/juaniwck/rb3e-homeassistant/actions/workflows/validate.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A Home Assistant custom integration that listens for [RB3Enhanced](https://rb3e.rbenhanced.rocks/) UDP event broadcasts from Rock Band 3, exposing the game state as entities and letting you drive your smart lights from Stage Kit lighting cues.

## Features

- **Game state sensor** — whether the game is at the menus or in a song, plus the current shell screen name.
- **Now playing sensors** — song title, artist, shortname, and venue.
- **Band sensor** — number of players, with each member's difficulty and instrument.
- **Stage Kit diagnostic sensor** — the last Stage Kit cue and a running packet count, so you can confirm cues are arriving.
- **Connectivity + platform sensors** — whether RB3E packets are being received (console IP as an attribute) and which console/emulator is sending them.
- **`rb3e_event` bus events** — every decoded packet (including every Stage Kit cue) is fired on the Home Assistant event bus for use in automations.
- **Built-in Stage Kit light driver (optional)** — maps the Stage Kit's red/green/blue/yellow LED rings onto RGB light entities with color blending, per-position (per-LED) mapping for driving many fixtures, rate limiting, an approximated strobe, and a fog-machine switch.

## Installation

### HACS (recommended)

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/juaniwck/rb3e-homeassistant` with category **Integration**.
3. Find **RB3Enhanced (Rock Band 3)** in HACS, install it, and restart Home Assistant.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=juaniwck&repository=rb3e-homeassistant&category=integration)

### Manual

Copy `custom_components/rb3e` into your Home Assistant `config/custom_components/` directory and restart Home Assistant.

### Then

1. In RB3Enhanced's `rb3.ini`, under `[Events]`, set `EnableEvents = true`. Broadcasts go to `255.255.255.255:21070` by default; if your console and Home Assistant are on different subnets/VLANs, set `BroadcastTarget` to your Home Assistant IP.
2. In Home Assistant, go to **Settings → Devices & Services → Add Integration → RB3Enhanced**. The default port (21070) matches RB3E's default.
3. Open the integration's **Configure** dialog to enable event firing and/or the Stage Kit light driver and map your lights.

## Entities

| Entity | Description |
|---|---|
| `sensor.rock_band_3_enhanced_game_state` | `menus` / `in_game` (screen name as attribute) |
| `sensor.rock_band_3_enhanced_song` | Current song title (artist, shortname, venue as attributes); clears at the menus |
| `sensor.rock_band_3_enhanced_artist` | Current song artist |
| `sensor.rock_band_3_enhanced_players` | Number of band members (per-member difficulty + instrument as `members` attribute) |
| `sensor.rock_band_3_enhanced_stage_kit` | *(diagnostic)* Last Stage Kit command + a running `packets_received` count — use this to confirm cues are arriving |
| `sensor.rock_band_3_enhanced_platform` | *(diagnostic)* Console/emulator RB3E is running on |
| `binary_sensor.rock_band_3_enhanced_in_song` | On while playing a song |
| `binary_sensor.rock_band_3_enhanced_connected` | On while packets are being received |

> **No score sensor.** Mainline RB3Enhanced does not broadcast score/stars packets (there is no `RB3E_EVENT_SCORE` send path in the firmware), so there's nothing to display. If you run a fork that does emit them, the data still comes through on the `rb3e_event` bus as `type: score`.

## The `rb3e_event` bus event

When "fire events" is enabled, every packet fires an event of type `rb3e_event`. Common payloads:

```yaml
# Game state change
{"type": "game_state", "state": "in_game", "in_game": true, "platform": "xbox360", "source": "192.168.1.50"}

# Song info (also song_artist, song_shortname, venue, screen)
{"type": "song_name", "value": "Everlong"}

# Score update
{"type": "score", "total_score": 152300, "member_scores": [152300, 0, 0, 0], "stars": 4}

# Stage Kit LED ring update (one event per color ring; mask is which of the 8 LEDs are lit)
{"type": "stagekit", "command": "led", "color": "red", "mask": 20, "leds": [2, 4], "count": 2, "raw_left": 20, "raw_right": 128}

# Stage Kit strobe (speed: slow | medium | fast | fastest | off)
{"type": "stagekit", "command": "strobe", "speed": "fast"}

# Stage Kit fog
{"type": "stagekit", "command": "fog", "on": true}

# Stage Kit everything off (fired e.g. when a song ends)
{"type": "stagekit", "command": "disable_all"}
```

You can watch these live in **Developer Tools → Events** by listening to `rb3e_event`.

## ⚠️ Stage Kit is Xbox 360 / Xenia only

In current RB3Enhanced the Stage Kit hook is compiled only for the Xbox 360 target (`#ifdef RB3E_XBOX`), so **`RB3E_EVENT_STAGEKIT` packets are broadcast only when RB3E runs on a real Xbox 360 or the Xenia emulator.** On Wii, Dolphin, PS3, or RPCS3 the game still sends game-state, song, and band packets, but **no Stage Kit lighting data at all** — so the light driver (and any lighting automation) will have nothing to react to, through no fault of this integration.

To check which platform you're on, look at `sensor.rock_band_3_enhanced_platform`, or watch the `packets_received` attribute on the Stage Kit sensor while a song plays. If that count never moves during a song, your platform isn't sending Stage Kit data.

## Built-in Stage Kit light driver

Enable it in the integration options and assign light entities. There are two ways to map, and you can use both together:

- **Color channels (red / green / blue / yellow):** whole-ring lights. Each mapped light represents that whole color ring; assign the same light to several channels and the active colors **blend** (weighted by how many LEDs are lit in each ring). This is the original behavior.
- **Positional lights:** map any number of RGB fixtures here and the engine spreads the Stage Kit's 8 LED slots across them, showing the **per-position blend** of all four colors. One fixture per slot gives you an 8-wide bar that mirrors the real pod's pattern; if red and blue are lit at the same slot, that fixture goes magenta. Map fewer than 8 and the slots group up, with brightness scaling to how many of each fixture's LEDs are lit. This is how you drive lots of lights while keeping the color mixing.

Notes:

- **Blending:** if you assign the same light to multiple channels (e.g. one lamp on both red and yellow), active colors are blended additively, weighted by how many LEDs in each ring are lit. Brightness scales with the fullest ring.
- **Rate limiting:** Stage Kit cues fire many times per second. The driver coalesces updates (latest state wins) and sends at most one command per light per interval. Hue over Zigbee typically needs 150–250 ms; WLED over UDP/API is happy at 50 ms.
- **Strobe:** approximated by toggling the strobe entities. Real strobe rates exceed what most network bulbs can do; for a proper strobe, point the strobe channel at a WLED device, or use the events to trigger a native WLED strobe effect instead.
- **Fog:** maps `fog on/off` to any switch/light/fan entity — e.g. a smart plug powering a fog machine with its remote jumpered on.
- **Menus:** by default everything turns off when the game returns to the menus.

## Example automations (event-based)

Announce the song on a media player:

```yaml
automation:
  - alias: "RB3: announce song"
    trigger:
      - platform: event
        event_type: rb3e_event
        event_data:
          type: game_state
          state: in_game
    action:
      - delay: "00:00:02"  # let song name/artist packets arrive
      - service: tts.speak
        target:
          entity_id: tts.home_assistant_cloud
        data:
          media_player_entity_id: media_player.living_room
          message: >-
            Now playing {{ states('sensor.rock_band_3_enhanced_song') }}
            by {{ states('sensor.rock_band_3_enhanced_artist') }}
```

Trigger a native WLED strobe effect instead of the built-in toggle strobe:

```yaml
  - alias: "RB3: WLED strobe on"
    trigger:
      - platform: event
        event_type: rb3e_event
        event_data:
          type: stagekit
          command: strobe
    action:
      - if: "{{ trigger.event.data.speed != 'off' }}"
        then:
          - service: light.turn_on
            target:
              entity_id: light.wled_stage
            data:
              effect: Strobe
        else:
          - service: light.turn_off
            target:
              entity_id: light.wled_stage
```

Flash a lamp red proportionally to the red ring:

```yaml
  - alias: "RB3: red ring"
    mode: restart
    trigger:
      - platform: event
        event_type: rb3e_event
        event_data:
          type: stagekit
          command: led
          color: red
    action:
      - if: "{{ trigger.event.data.count > 0 }}"
        then:
          - service: light.turn_on
            target:
              entity_id: light.lamp
            data:
              rgb_color: [255, 0, 0]
              brightness: "{{ (trigger.event.data.count / 8 * 255) | round }}"
        else:
          - service: light.turn_off
            target:
              entity_id: light.lamp
```

## Protocol notes

The wire format is defined in RB3Enhanced's `include/net_events.h`: an 8-byte header (`RB3E` magic, protocol version 0, packet type, payload size, platform ID — the header magic is big-endian) followed by the payload. Stage Kit packets carry the two raw rumble bytes the game would send a physical Stage Kit pod: the **left** channel is an 8-bit LED bitmask (which of a color ring's 8 LEDs are lit), and the **right** channel is the command — `0x20/0x40/0x60/0x80` selects the blue/green/yellow/red ring, `0x01/0x02` is fog on/off, `0x03`–`0x06` are strobe speeds, `0x07` strobe off, and `0xFF` all-off. These values are confirmed against the GPL-3.0 [StageKitPied](https://github.com/Blasteroids/StageKitPied) project's `StageKitConsts.h`. The decode table lives in `protocol.py` (`SK_*` constants) — if a value is ever wrong for your setup, that's the one place to fix it.

## Troubleshooting

- **Connected sensor stays off:** confirm `EnableEvents = true` in `rb3.ini`, that console and HA are on the same L2 network (broadcast doesn't cross routers — otherwise set `BroadcastTarget` to the HA IP), and that nothing else on the HA host is bound to UDP 21070.
- **The light driver does nothing:** work down this list.
  1. Check `sensor.rock_band_3_enhanced_stage_kit` while a song plays — if `packets_received` never increases, no Stage Kit cues are being sent. The usual cause is running on a non-Xbox platform (see the Xbox-only note above); confirm with `sensor.rock_band_3_enhanced_platform`.
  2. Make sure **"Enable built-in Stage Kit light driver"** is toggled on in the integration's options (mapping lights isn't enough on its own).
  3. Confirm the lights you mapped are actually RGB-capable `light.` entities and are reachable.
  4. Turn on debug logging to watch cues flow into the driver and the resulting service calls:
     ```yaml
     logger:
       logs:
         custom_components.rb3e: debug
     ```
     You'll see `Stage Kit cue: {...}` lines and matching `light.turn_on ...` lines. Cues but no turn_on lines means a mapping problem; turn_on lines but no visible change means a light/transition issue on the device side.
- **Lights lag behind the music:** increase the minimum update interval, or reduce how many entities are mapped. Zigbee groups are cheaper than many individual bulbs.
- **Running RB3E on Dolphin/Xenia on the same machine as HA:** another RB3E tool (e.g. the loopback Stage Kit app) may already be bound to the port; only one listener can bind it unless it uses SO_REUSEADDR.

## License & credits

Released under the **GNU General Public License v3.0** (see `LICENSE`). The Stage Kit command values were verified against [StageKitPied](https://github.com/Blasteroids/StageKitPied) by Blasteroids (GPL-3.0), and the protocol layout against [RB3Enhanced](https://github.com/RBEnhanced/RB3Enhanced). Thanks to both projects.
