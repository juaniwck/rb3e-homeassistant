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
OPT_LIGHTS_POSITIONS = "lights_positions"
OPT_LIGHTS_STROBE = "lights_strobe"
OPT_FOG_ENTITIES = "fog_entities"
OPT_MAX_BRIGHTNESS = "max_brightness"
OPT_MIN_INTERVAL_MS = "min_update_interval_ms"
OPT_TURN_OFF_IN_MENUS = "turn_off_in_menus"

DEFAULT_MAX_BRIGHTNESS = 255
DEFAULT_MIN_INTERVAL_MS = 100

# Home Assistant bus event type fired for every RB3E packet (when enabled)
HA_EVENT_TYPE = "rb3e_event"

# Dispatcher signal (per config entry): f"{SIGNAL_UPDATE}_{entry_id}"
SIGNAL_UPDATE = "rb3e_update"

CONNECTION_TIMEOUT_S = 60  # no packets for this long -> considered disconnected
