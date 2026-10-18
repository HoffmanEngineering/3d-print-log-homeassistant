"""The 3D Print Log integration."""

from __future__ import annotations

import logging
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import PrintLogClient
from .const import CONF_API_KEY, CONF_BASE_URL, DOMAIN
from .coordinator import PrintLogData, SlowCoordinator, SnapshotCoordinator
from .events import BUS_EVENT, PrintEvent, events_payload
from .repairs import async_clear_unsupported_schema
from .services import async_register_services
from .store import EventStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT]

type PrintLogConfigEntry = ConfigEntry[PrintLogData]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once; they look up the loaded entries when called."""
    async_register_services(hass)
    return True


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
    # Before async_start_dispatch, so events of the first refresh are fired too.
    entry.async_on_unload(snapshot.async_add_event_listener(partial(_fire_bus_events, hass, entry)))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        # Nothing was announced, so don't persist the state that saw it.
        snapshot.async_discard_pending_events()
        raise
    snapshot.async_start_dispatch()
    return True


@callback
def _fire_bus_events(
    hass: HomeAssistant, entry: PrintLogConfigEntry, events: list[PrintEvent]
) -> None:
    """Fire each print event on the Home Assistant bus (spec 6.3)."""
    registry = dr.async_get(hass)
    uid = entry.runtime_data.snapshot.entry_uid
    for event in events:
        try:
            device = registry.async_get_device_by_identifier(
                (DOMAIN, f"{uid}:printer:{event.printer_id}"), entry.entry_id
            )
            device_id = device.id if device is not None else None
            hass.bus.async_fire(
                BUS_EVENT[event.status], events_payload(event, entry.entry_id, device_id)
            )
        except Exception:
            _LOGGER.exception("Could not fire print event %s", event)


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
