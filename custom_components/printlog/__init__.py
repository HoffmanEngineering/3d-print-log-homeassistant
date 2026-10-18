"""The 3D Print Log integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PrintLogAuthError, PrintLogClient, PrintLogError
from .const import CONF_BASE_URL
from .coordinator import PrintLogData

_LOGGER = logging.getLogger(__name__)

type PrintLogConfigEntry = ConfigEntry[PrintLogData]


async def async_setup_entry(hass: HomeAssistant, entry: PrintLogConfigEntry) -> bool:
    """Validate the credentials and store the client."""
    client = PrintLogClient(
        async_get_clientsession(hass), entry.data[CONF_BASE_URL], entry.data["api_key"]
    )
    try:
        await client.get_me()
    except PrintLogAuthError as err:
        raise ConfigEntryAuthFailed("The API key was rejected") from err
    except PrintLogError as err:
        raise ConfigEntryNotReady("Could not reach 3D Print Log") from err
    entry.runtime_data = PrintLogData(client=client)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PrintLogConfigEntry) -> bool:
    """Unload a config entry."""
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry. Entries from a newer version are rejected."""
    if entry.version > 1:
        _LOGGER.error(
            "Config entry version %s.%s is newer than this integration supports",
            entry.version,
            entry.minor_version,
        )
        return False
    return True
