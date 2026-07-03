"""Config flow for the RB3Enhanced integration."""

from __future__ import annotations

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
    DOMAIN,
    OPT_FIRE_EVENTS,
    OPT_FOG_ENTITIES,
    OPT_LIGHT_DRIVER,
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


class Rb3eOptionsFlow(OptionsFlow):
    """Options: event firing + Stage Kit light driver mapping."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # NumberSelector returns floats; store ints.
            user_input[OPT_MAX_BRIGHTNESS] = int(user_input[OPT_MAX_BRIGHTNESS])
            user_input[OPT_MIN_INTERVAL_MS] = int(user_input[OPT_MIN_INTERVAL_MS])
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options

        schema = vol.Schema(
            {
                vol.Required(
                    OPT_FIRE_EVENTS, default=opts.get(OPT_FIRE_EVENTS, True)
                ): selector.BooleanSelector(),
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
                    OPT_LIGHTS_POSITIONS,
                    description={"suggested_value": opts.get(OPT_LIGHTS_POSITIONS)},
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
                vol.Required(
                    OPT_TURN_OFF_IN_MENUS,
                    default=opts.get(OPT_TURN_OFF_IN_MENUS, True),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
