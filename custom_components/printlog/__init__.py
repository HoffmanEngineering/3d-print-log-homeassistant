"""The 3D Print Log integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PrintLogClient
from .const import CONF_API_KEY, CONF_BASE_URL
from .coordinator import PrintLogData, SlowCoordinator, SnapshotCoordinator
from .repairs import async_clear_unsupported_schema
from .store import EventStore

_LOGGER = logging.getLogger(__name__)

# Later tasks add their platforms here (event).
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

type PrintLogConfigEntry = ConfigEntry[PrintLogData]


async def async_setup_entry(hass: HomeAssistant, entry: PrintLogConfigEntry) -> bool:
    """Set up the coordinators, load the event store and forward the platforms."""
    client = PrintLogClient(
        async_get_clientsession(hass), entry.data[CONF_BASE_URL], entry.data[CONF_API_KEY]
    )
    store = EventStore(hass, entry.entry_id)
    event_state = await store.async_load()
    snapshot = SnapshotCoordinator(hass, entry, client, store, event_state)
    slow = SlowCoordinator(hass, entry, client)

    # An auth failure raises ConfigEntryAuthFailed (reauth); anything else
    # raises ConfigEntryNotReady (retry).
    await snapshot.async_config_entry_first_refresh()
    # The unread count is optional: its failure never blocks setup.
    await slow.async_refresh()

    entry.runtime_data = PrintLogData(client=client, snapshot=snapshot, slow=slow, store=store)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        # Nothing was announced, so don't persist the state that saw it.
        snapshot.async_discard_pending_events()
        raise
    snapshot.async_start_dispatch()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: PrintLogConfigEntry) -> None:
    """Reload when the options (scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PrintLogConfigEntry) -> bool:
    """Unload a config entry, writing any pending event state first."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = entry.runtime_data
        # Stop polling first. A refresh already in flight finds the store
        # closed and neither saves nor dispatches.
        await data.snapshot.async_shutdown()
        await data.slow.async_shutdown()
        await data.store.async_flush()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: PrintLogConfigEntry) -> None:
    """Delete the event store and any repair issue when the entry is removed."""
    await EventStore(hass, entry.entry_id).async_remove()
    async_clear_unsupported_schema(hass, entry)


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
