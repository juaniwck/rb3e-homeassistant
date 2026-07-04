"""Parser for the RB3Enhanced local network events protocol (version 0).

Wire format (see RB3Enhanced include/net_events.h):

    struct RB3E_EventHeader {          // packed, big-endian (PowerPC)
        int  ProtocolMagic;            // 'RB3E' = 0x52423345
        char ProtocolVersion;          // 0
        char PacketType;               // RB3E_Events_EventTypes
        char PacketSize;               // payload size, max 0xFF
        char Platform;                 // RB3E_Events_PlatformIDs
    };

Packets are UDP, broadcast by default to port 21070 (0x524E, 'RN').
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

MAGIC = b"RB3E"
PROTOCOL_VERSION = 0
DEFAULT_PORT = 0x524E  # 21070
HEADER_LEN = 8

# RB3E_Events_EventTypes
EVENT_ALIVE = 0
EVENT_STATE = 1
EVENT_SONG_NAME = 2
EVENT_SONG_ARTIST = 3
EVENT_SONG_SHORTNAME = 4
EVENT_SCORE = 5
EVENT_STAGEKIT = 6
EVENT_BAND_INFO = 7
EVENT_VENUE_NAME = 8
EVENT_SCREEN_NAME = 9
EVENT_DX_DATA = 10

EVENT_NAMES = {
    EVENT_ALIVE: "alive",
    EVENT_STATE: "game_state",
    EVENT_SONG_NAME: "song_name",
    EVENT_SONG_ARTIST: "song_artist",
    EVENT_SONG_SHORTNAME: "song_shortname",
    EVENT_SCORE: "score",
    EVENT_STAGEKIT: "stagekit",
    EVENT_BAND_INFO: "band_info",
    EVENT_VENUE_NAME: "venue",
    EVENT_SCREEN_NAME: "screen",
    EVENT_DX_DATA: "dx_data",
}

PLATFORMS = {
    0: "xbox360",
    1: "xenia",
    2: "wii",
    3: "dolphin",
    4: "ps3",
    5: "rpcs3",
    0xFF: "unknown",
}

DIFFICULTIES = {0: "easy", 1: "medium", 2: "hard", 3: "expert"}

# RB3 TrackType enum (include/rb3/BandUser.h)
TRACK_TYPES = {
    0: "drums",
    1: "guitar",
    2: "bass",
    3: "vocals",
    4: "keys",
    5: "pro_keys",
    6: "pro_guitar",
    7: "harmonies",
    8: "pro_bass",
}

# ---------------------------------------------------------------------------
# Stage Kit command decode.
#
# The RB3E_EVENT_STAGEKIT payload is the raw pair of XInput rumble bytes the
# game sends to a physical Stage Kit pod: LeftChannel is the parameter
# ("weight"), RightChannel is the command. If any of these values turn out to
# be wrong for your hardware/version, this table is the only place to fix.
# ---------------------------------------------------------------------------
SK_FOG_ON = 0x01
SK_FOG_OFF = 0x02
SK_STROBE_SPEEDS = {0x03: "slow", 0x04: "medium", 0x05: "fast", 0x06: "fastest"}
SK_STROBE_OFF = 0x07
SK_LED_COLORS = {0x20: "blue", 0x40: "green", 0x60: "yellow", 0x80: "red"}
SK_DISABLE_ALL = 0xFF

LED_COLOR_RGB = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 190, 0),
}


@dataclass
class Rb3eEvent:
    """A decoded RB3Enhanced event."""

    type: int
    type_name: str
    platform: str
    data: dict[str, Any] = field(default_factory=dict)


def _string(payload: bytes) -> str:
    """Decode a null-terminated string payload."""
    return payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def decode_stagekit(left: int, right: int) -> dict[str, Any]:
    """Decode a Stage Kit (left, right) rumble pair into a friendly dict."""
    out: dict[str, Any] = {"raw_left": left, "raw_right": right}
    if right == SK_DISABLE_ALL:
        out["command"] = "disable_all"
    elif right == SK_FOG_ON:
        out.update(command="fog", on=True)
    elif right == SK_FOG_OFF:
        out.update(command="fog", on=False)
    elif right in SK_STROBE_SPEEDS:
        out.update(command="strobe", speed=SK_STROBE_SPEEDS[right])
    elif right == SK_STROBE_OFF:
        out.update(command="strobe", speed="off")
    elif right in SK_LED_COLORS:
        leds = [i for i in range(8) if left & (1 << i)]
        out.update(
            command="led",
            color=SK_LED_COLORS[right],
            mask=left,
            leds=leds,
            count=len(leds),
        )
    else:
        out["command"] = "unknown"
    return out


def parse_packet(data: bytes) -> Rb3eEvent | None:
    """Parse a raw UDP datagram. Returns None if it isn't a valid RB3E packet."""
    if len(data) < HEADER_LEN or data[0:4] != MAGIC:
        return None
    version, ptype, psize, platform = data[4], data[5], data[6], data[7]
    if version != PROTOCOL_VERSION:
        return None
    payload = data[HEADER_LEN : HEADER_LEN + psize]

    event = Rb3eEvent(
        type=ptype,
        type_name=EVENT_NAMES.get(ptype, f"unknown_{ptype}"),
        platform=PLATFORMS.get(platform, "unknown"),
    )

    if ptype == EVENT_ALIVE:
        event.data["build_tag"] = _string(payload)
    elif ptype == EVENT_STATE:
        in_game = bool(payload and payload[0] == 1)
        event.data["state"] = "in_game" if in_game else "menus"
        event.data["in_game"] = in_game
    elif ptype in (
        EVENT_SONG_NAME,
        EVENT_SONG_ARTIST,
        EVENT_SONG_SHORTNAME,
        EVENT_VENUE_NAME,
        EVENT_SCREEN_NAME,
    ):
        event.data["value"] = _string(payload)
    elif ptype == EVENT_SCORE:
        # struct: int TotalScore; int MemberScores[4]; char Stars; (big-endian)
        if len(payload) >= 21:
            total, m0, m1, m2, m3, stars = struct.unpack_from(">iiiiib", payload)
            event.data.update(
                total_score=total,
                member_scores=[m0, m1, m2, m3],
                stars=stars,
            )
    elif ptype == EVENT_STAGEKIT:
        if len(payload) >= 2:
            event.data.update(decode_stagekit(payload[0], payload[1]))
    elif ptype == EVENT_BAND_INFO:
        # struct: char MemberExists[4]; char Difficulty[4]; char TrackType[4];
        if len(payload) >= 12:
            members = []
            for i in range(4):
                members.append(
                    {
                        "exists": bool(payload[i]),
                        "difficulty": DIFFICULTIES.get(payload[4 + i], payload[4 + i]),
                        "track_type": TRACK_TYPES.get(payload[8 + i], payload[8 + i]),
                    }
                )
            event.data["members"] = members
    elif ptype == EVENT_DX_DATA:
        # struct: char IdentifyValue[10]; char String[240];
        if len(payload) >= 10:
            event.data["identify"] = _string(payload[:10])
            event.data["value"] = _string(payload[10:])

    return event
