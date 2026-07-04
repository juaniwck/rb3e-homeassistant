"""Config flow for the RB3Enhanced integration."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_PORT,
    DEFAULT_MAX_BRIGHTNESS,
    DEFAULT_MIN_INTERVAL_MS,
    DEFAULT_PORT,
    DEFAULT_WLED_LED_COUNT,
    DEFAULT_WLED_MIN_INTERVAL_MS,
    DEFAULT_WLED_PORT,
    DEFAULT_WLED_TIMEOUT_S,
    DOMAIN,
    OPT_FIRE_EVENTS,
    OPT_FOG_ENTITIES,
    OPT_LIGHT_DRIVER,
    OPT_LIGHTS_BLUE,
    OPT_LIGHTS_GREEN,
    OPT_LIGHTS_RED,
    OPT_LIGHTS_STROBE,
    OPT_LIGHTS_YELLOW,
    OPT_MAX_BRIGHTNESS,
    OPT_MIN_INTERVAL_MS,
    OPT_TURN_OFF_IN_MENUS,
    OPT_WLED_CUSTOM_MAP,
    OPT_WLED_ENABLED,
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
    OPT_ZONES_BLUE,
    OPT_ZONES_GREEN,
    OPT_ZONES_RED,
    OPT_ZONES_YELLOW,
    WLED_MODE_BLEND,
    WLED_MODE_INTERLEAVE,
)

LIGHT_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="light", multiple=True)
)
FOG_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["switch", "light", "fan"], multiple=True)
)


class Rb3eConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(f"rb3e_{port}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Rock Band 3 Enhanced",
                data={CONF_PORT: port},
                options={OPT_FIRE_EVENTS: True, OPT_LIGHT_DRIVER: False},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=65535, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return Rb3eOptionsFlow()


ENTITY_LIST_OPTS = (
    OPT_LIGHTS_RED,
    OPT_LIGHTS_GREEN,
    OPT_LIGHTS_BLUE,
    OPT_LIGHTS_YELLOW,
    OPT_ZONES_RED,
    OPT_ZONES_GREEN,
    OPT_ZONES_BLUE,
    OPT_ZONES_YELLOW,
    OPT_LIGHTS_STROBE,
    OPT_FOG_ENTITIES,
)


class Rb3eOptionsFlow(OptionsFlow):
    """Options, split into a menu: general, entity light engine, WLED engine."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "ha_lights", "wled"],
        )

    def _save(self, updates: dict[str, Any]) -> ConfigFlowResult:
        """Merge one step's answers into the full options dict and save."""
        options = dict(self.config_entry.options)
        options.update(updates)
        return self.async_create_entry(title="", data=options)

    # ------------------------------------------------------------- general

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = self.config_entry.options
        if user_input is not None:
            return self._save(user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    OPT_FIRE_EVENTS, default=opts.get(OPT_FIRE_EVENTS, True)
                ): selector.BooleanSelector(),
                vol.Required(
                    OPT_TURN_OFF_IN_MENUS,
                    default=opts.get(OPT_TURN_OFF_IN_MENUS, True),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema)

    # ------------------------------------------- entity light engine (slow)

    async def async_step_ha_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = self.config_entry.options
        if user_input is not None:
            # NumberSelector returns floats; store ints.
            user_input[OPT_MAX_BRIGHTNESS] = int(user_input[OPT_MAX_BRIGHTNESS])
            user_input[OPT_MIN_INTERVAL_MS] = int(user_input[OPT_MIN_INTERVAL_MS])
            # Optional entity selectors are omitted from user_input when
            # cleared; store explicit empty lists so clearing sticks.
            for key in ENTITY_LIST_OPTS:
                user_input[key] = user_input.get(key, [])
            return self._save(user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    OPT_LIGHT_DRIVER, default=opts.get(OPT_LIGHT_DRIVER, False)
                ): selector.BooleanSelector(),
                vol.Optional(
                    OPT_LIGHTS_RED,
                    description={"suggested_value": opts.get(OPT_LIGHTS_RED)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_LIGHTS_GREEN,
                    description={"suggested_value": opts.get(OPT_LIGHTS_GREEN)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_LIGHTS_BLUE,
                    description={"suggested_value": opts.get(OPT_LIGHTS_BLUE)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_LIGHTS_YELLOW,
                    description={"suggested_value": opts.get(OPT_LIGHTS_YELLOW)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_ZONES_RED,
                    description={"suggested_value": opts.get(OPT_ZONES_RED)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_ZONES_GREEN,
                    description={"suggested_value": opts.get(OPT_ZONES_GREEN)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_ZONES_BLUE,
                    description={"suggested_value": opts.get(OPT_ZONES_BLUE)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_ZONES_YELLOW,
                    description={"suggested_value": opts.get(OPT_ZONES_YELLOW)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_LIGHTS_STROBE,
                    description={"suggested_value": opts.get(OPT_LIGHTS_STROBE)},
                ): LIGHT_SELECTOR,
                vol.Optional(
                    OPT_FOG_ENTITIES,
                    description={"suggested_value": opts.get(OPT_FOG_ENTITIES)},
                ): FOG_SELECTOR,
                vol.Required(
                    OPT_MAX_BRIGHTNESS,
                    default=opts.get(OPT_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=255, mode=selector.NumberSelectorMode.SLIDER
                    )
                ),
                vol.Required(
                    OPT_MIN_INTERVAL_MS,
                    default=opts.get(OPT_MIN_INTERVAL_MS, DEFAULT_MIN_INTERVAL_MS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=20,
                        max=1000,
                        step=10,
                        unit_of_measurement="ms",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="ha_lights", data_schema=schema)

    # ------------------------------------------------ WLED UDP engine (fast)

    async def async_step_wled(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            for key in (
                OPT_WLED_PORT,
                OPT_WLED_LED_COUNT,
                OPT_WLED_LED_START,
                OPT_WLED_MAX_BRIGHTNESS,
                OPT_WLED_TIMEOUT_S,
                OPT_WLED_MIN_INTERVAL_MS,
            ):
                user_input[key] = int(user_input[key])
            user_input[OPT_WLED_HOST] = user_input.get(OPT_WLED_HOST, "").strip()
            user_input[OPT_WLED_CUSTOM_MAP] = user_input.get(
                OPT_WLED_CUSTOM_MAP, ""
            ).strip()
            if user_input[OPT_WLED_ENABLED] and not user_input[OPT_WLED_HOST]:
                errors[OPT_WLED_HOST] = "wled_host_required"
            if user_input[OPT_WLED_CUSTOM_MAP]:
                try:
                    parsed = json.loads(user_input[OPT_WLED_CUSTOM_MAP])
                    if not isinstance(parsed, dict):
                        raise ValueError
                except ValueError:
                    errors[OPT_WLED_CUSTOM_MAP] = "invalid_custom_map"
            if not errors:
                return self._save(user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    OPT_WLED_ENABLED, default=opts.get(OPT_WLED_ENABLED, False)
                ): selector.BooleanSelector(),
                vol.Optional(
                    OPT_WLED_HOST,
                    description={"suggested_value": opts.get(OPT_WLED_HOST, "")},
                ): selector.TextSelector(),
                vol.Required(
                    OPT_WLED_PORT, default=opts.get(OPT_WLED_PORT, DEFAULT_WLED_PORT)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=65535, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPT_WLED_LED_COUNT,
                    default=opts.get(OPT_WLED_LED_COUNT, DEFAULT_WLED_LED_COUNT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=2000, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPT_WLED_LED_START, default=opts.get(OPT_WLED_LED_START, 0)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=2000, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    OPT_WLED_REVERSE, default=opts.get(OPT_WLED_REVERSE, False)
                ): selector.BooleanSelector(),
                vol.Required(
                    OPT_WLED_MODE, default=opts.get(OPT_WLED_MODE, WLED_MODE_BLEND)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[WLED_MODE_BLEND, WLED_MODE_INTERLEAVE],
                        translation_key="wled_mode",
                    )
                ),
                vol.Required(
                    OPT_WLED_MAX_BRIGHTNESS,
                    default=opts.get(OPT_WLED_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=255, mode=selector.NumberSelectorMode.SLIDER
                    )
                ),
                vol.Required(
                    OPT_WLED_STROBE_ENABLED,
                    default=opts.get(OPT_WLED_STROBE_ENABLED, True),
                ): selector.BooleanSelector(),
                vol.Required(
                    OPT_WLED_TIMEOUT_S,
                    default=opts.get(OPT_WLED_TIMEOUT_S, DEFAULT_WLED_TIMEOUT_S),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=254,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    OPT_WLED_MIN_INTERVAL_MS,
                    default=opts.get(
                        OPT_WLED_MIN_INTERVAL_MS, DEFAULT_WLED_MIN_INTERVAL_MS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=200,
                        step=5,
                        unit_of_measurement="ms",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Optional(
                    OPT_WLED_CUSTOM_MAP,
                    description={"suggested_value": opts.get(OPT_WLED_CUSTOM_MAP, "")},
                ): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )
        return self.async_show_form(step_id="wled", data_schema=schema, errors=errors)
