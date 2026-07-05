"""Constants for the RB3Enhanced integration."""

DOMAIN = "rb3e"

CONF_PORT = "port"
DEFAULT_PORT = 21070  # 0x524E, RB3E default broadcast port

# Options
OPT_FIRE_EVENTS = "fire_events"
OPT_LIGHT_DRIVER = "light_driver_enabled"
OPT_LIGHTS_RED = "lights_red"
OPT_LIGHTS_GREEN = "lights_green"
OPT_LIGHTS_BLUE = "lights_blue"
OPT_LIGHTS_YELLOW = "lights_yellow"
OPT_LIGHTS_STROBE = "lights_strobe"
OPT_FOG_ENTITIES = "fog_entities"
# Ordered zone lists (up to 8 entities per color). Each of the Stage Kit's
# 8 LEDs per color ring maps onto these entities in order, ESP32-SK style.
OPT_ZONES_RED = "zones_red"
OPT_ZONES_GREEN = "zones_green"
OPT_ZONES_BLUE = "zones_blue"
OPT_ZONES_YELLOW = "zones_yellow"
OPT_MAX_BRIGHTNESS = "max_brightness"
OPT_MIN_INTERVAL_MS = "min_update_interval_ms"
OPT_TURN_OFF_IN_MENUS = "turn_off_in_menus"
# Black out during the endgame/score screens instead of rendering the game's
# built-in score-screen light show (game_state is still "in_game" there).
OPT_OFF_ON_SCORE_SCREENS = "turn_off_on_score_screens"

DEFAULT_MAX_BRIGHTNESS = 255
DEFAULT_MIN_INTERVAL_MS = 100

# WLED direct UDP engine (fast, per-LED). Independent from the entity engine.
OPT_WLED_ENABLED = "wled_enabled"
OPT_WLED_HOST = "wled_host"
OPT_WLED_PORT = "wled_port"
OPT_WLED_LED_COUNT = "wled_led_count"
OPT_WLED_LED_START = "wled_led_start"
OPT_WLED_REVERSE = "wled_reverse"
OPT_WLED_MODE = "wled_mode"  # blend | interleave (custom map overrides)
OPT_WLED_MAX_BRIGHTNESS = "wled_max_brightness"
OPT_WLED_TIMEOUT_S = "wled_timeout_s"
OPT_WLED_MIN_INTERVAL_MS = "wled_min_interval_ms"
OPT_WLED_STROBE_ENABLED = "wled_strobe_enabled"
OPT_WLED_CUSTOM_MAP = "wled_custom_map"

WLED_MODE_BLEND = "blend"
WLED_MODE_INTERLEAVE = "interleave"

DEFAULT_WLED_PORT = 21324
DEFAULT_WLED_LED_COUNT = 30
DEFAULT_WLED_TIMEOUT_S = 2
DEFAULT_WLED_MIN_INTERVAL_MS = 20

# Home Assistant bus event type fired for every RB3E packet (when enabled)
HA_EVENT_TYPE = "rb3e_event"

# Dispatcher signal (per config entry): f"{SIGNAL_UPDATE}_{entry_id}"
SIGNAL_UPDATE = "rb3e_update"

CONNECTION_TIMEOUT_S = 60  # no packets for this long -> considered disconnected
