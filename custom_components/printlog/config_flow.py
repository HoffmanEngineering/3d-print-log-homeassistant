"""Config flow for the 3D Print Log integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    PrintLogAuthError,
    PrintLogClient,
    PrintLogError,
    normalized_host,
)
from .const import (
    AUTH_TYPE_API_KEY,
    CONF_AUTH_TYPE,
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

CONF_API_KEY = "api_key"
DEFAULT_TITLE = "3D Print Log"

KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


class _InvalidUrl(Exception):
    """The base URL could not be parsed."""


def _user_schema(base_url: str = DEFAULT_BASE_URL) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_BASE_URL, default=base_url): URL_SELECTOR,
            vol.Required(CONF_API_KEY): KEY_SELECTOR,
        }
    )


class PrintLogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for 3D Print Log."""

    VERSION = 1
    MINOR_VERSION = 1

    async def _validate(self, base_url: str, api_key: str) -> tuple[str, str, str]:
        """Return (clean base_url, unique_id, title); raise on failure."""
        base_url = base_url.strip().rstrip("/")
        try:
            host = normalized_host(base_url)
        except ValueError as err:
            raise _InvalidUrl from err
        if not host:
            raise _InvalidUrl
        client = PrintLogClient(async_get_clientsession(self.hass), base_url, api_key)
        me = await client.get_me()
        title = me.get("displayName") or DEFAULT_TITLE
        return base_url, f"{host}:{me['id']}", title

    async def _try_validate(
        self, base_url: str, api_key: str, errors: dict[str, str]
    ) -> tuple[str, str, str] | None:
        try:
            return await self._validate(base_url, api_key)
        except _InvalidUrl:
            errors["base"] = "invalid_url"
        except PrintLogAuthError:
            errors["base"] = "invalid_auth"
        except PrintLogError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error validating the 3D Print Log connection")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            validated = await self._try_validate(
                user_input[CONF_BASE_URL], user_input[CONF_API_KEY], errors
            )
            if validated:
                base_url, unique_id, title = validated
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_BASE_URL: base_url,
                        CONF_AUTH_TYPE: AUTH_TYPE_API_KEY,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                    },
                )
        schema = self.add_suggested_values_to_schema(
            _user_schema(), user_input and {CONF_BASE_URL: user_input[CONF_BASE_URL]}
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            validated = await self._try_validate(
                entry.data[CONF_BASE_URL], user_input[CONF_API_KEY], errors
            )
            if validated:
                await self.async_set_unique_id(validated[1])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): KEY_SELECTOR}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            validated = await self._try_validate(
                user_input[CONF_BASE_URL], user_input[CONF_API_KEY], errors
            )
            if validated:
                base_url, unique_id, _ = validated
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_BASE_URL: base_url,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                    },
                )
        base_url = (user_input or entry.data)[CONF_BASE_URL]
        return self.async_show_form(
            step_id="reconfigure", data_schema=_user_schema(base_url), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> PrintLogOptionsFlow:
        return PrintLogOptionsFlow()


class PrintLogOptionsFlow(OptionsFlow):
    """Options: the polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
