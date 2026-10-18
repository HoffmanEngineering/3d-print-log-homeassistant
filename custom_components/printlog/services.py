"""Actions of the 3D Print Log integration (spec 7)."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Awaitable
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .api import (
    PrintLogAuthError,
    PrintLogConnectionError,
    PrintLogNotFoundError,
    PrintLogRateLimitError,
    PrintLogUnsupportedError,
    PrintLogValidationError,
)
from .const import DOMAIN
from .models import Snapshot

SERVICE_ADJUST_REMAINING = "adjust_remaining"
SERVICE_SET_SPOOL_ACTIVE = "set_spool_active"
SERVICE_LOG_MAINTENANCE = "log_maintenance"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ENTITY_ID = "entity_id"
ATTR_SPOOL_ID = "spool_id"
ATTR_SPOOL_NUMBER = "spool_number"
ATTR_AMOUNT = "amount"
ATTR_UNIT = "unit"
ATTR_NOTES = "notes"
ATTR_ACTIVE = "active"
ATTR_DEVICE_ID = "device_id"
ATTR_CATEGORY = "category"
ATTR_DESCRIPTION = "description"
ATTR_DATE = "date"
ATTR_MAINTENANCE_ID = "maintenance_id"

UNIT_SOURCES = {"g": "weight", "mm": "length", "ml": "volume"}

_SLOT_UNIQUE_ID = re.compile(r":printer:(\d+):slot:(\d+):(filament|remaining)$")
_PRINTER_IDENTIFIER = re.compile(r":printer:(\d+)$")

_SPOOL_CHOICE = {
    vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
    vol.Optional(ATTR_SPOOL_ID): cv.string,
    vol.Optional(ATTR_SPOOL_NUMBER): vol.All(vol.Coerce(int), vol.Range(min=1)),
}

ADJUST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        **_SPOOL_CHOICE,
        vol.Required(ATTR_AMOUNT): vol.Coerce(float),
        vol.Optional(ATTR_UNIT, default="g"): vol.In(tuple(UNIT_SOURCES)),
        vol.Optional(ATTR_NOTES): vol.All(cv.string, vol.Length(max=1000)),
    }
)

SET_ACTIVE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        **_SPOOL_CHOICE,
        vol.Required(ATTR_ACTIVE): cv.boolean,
    }
)

MAINTENANCE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_CATEGORY): vol.All(cv.string, vol.Length(max=256)),
        vol.Required(ATTR_DESCRIPTION): vol.All(cv.string, vol.Length(min=1, max=1000)),
        vol.Optional(ATTR_NOTES): vol.All(cv.string, vol.Length(max=1000)),
        vol.Optional(ATTR_DATE): cv.datetime,
        vol.Optional(ATTR_MAINTENANCE_ID): cv.string,
    }
)


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _loaded_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    return [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]


def _entry_by_id(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise _error("entry_not_found")
    if entry.state is not ConfigEntryState.LOADED:
        raise _error("entry_not_loaded")
    return entry


def _pick_entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry:
    """The entry named by config_entry_id, or the only loaded one."""
    if entry_id:
        return _entry_by_id(hass, entry_id)
    entries = _loaded_entries(hass)
    if not entries:
        raise _error("no_entry")
    if len(entries) > 1:
        raise _error("config_entry_required")
    return entries[0]


def _resolve_slot_entity(hass: HomeAssistant, entity_id: str) -> tuple[ConfigEntry, int, int]:
    """(entry, printer id, slot) of a printlog slot_filament or slot_remaining sensor."""
    reg_entry = er.async_get(hass).async_get(entity_id)
    if reg_entry is None or reg_entry.platform != DOMAIN or not reg_entry.unique_id:
        raise _error("invalid_slot_entity")
    match = _SLOT_UNIQUE_ID.search(reg_entry.unique_id)
    if match is None or reg_entry.config_entry_id is None:
        raise _error("invalid_slot_entity")
    return (
        _entry_by_id(hass, reg_entry.config_entry_id),
        int(match.group(1)),
        int(match.group(2)),
    )


def _spool_id_from_slot(snapshot: Snapshot, printer_id: int, slot: int) -> str:
    printer = next((p for p in snapshot.printers if p.id == printer_id), None)
    loaded = printer.loaded_in_slot(slot) if printer is not None else None
    if loaded is None:
        raise _error("slot_empty")
    return loaded.spool_id


def _normalized(spool_id: str) -> str:
    try:
        return str(uuid.UUID(spool_id))
    except ValueError:
        return spool_id


def _resolve_spool(hass: HomeAssistant, data: dict[str, Any]) -> tuple[ConfigEntry, str]:
    """The entry and the spool id the call targets."""
    chosen = [k for k in (ATTR_ENTITY_ID, ATTR_SPOOL_ID, ATTR_SPOOL_NUMBER) if k in data]
    if len(chosen) != 1:
        raise _error("spool_choice")
    if ATTR_ENTITY_ID in data:
        entry, printer_id, slot = _resolve_slot_entity(hass, data[ATTR_ENTITY_ID])
        snapshot = entry.runtime_data.snapshot.data
        return entry, _spool_id_from_slot(snapshot, printer_id, slot)
    entry = _pick_entry(hass, data.get(ATTR_CONFIG_ENTRY_ID))
    snapshot = entry.runtime_data.snapshot.data
    if ATTR_SPOOL_NUMBER in data:
        spool = snapshot.spool_by_number(data[ATTR_SPOOL_NUMBER])
        if spool is None:
            raise _error("spool_not_found")
        return entry, spool.id
    return entry, data[ATTR_SPOOL_ID]


async def _call[T](awaitable: Awaitable[T]) -> T:
    """Await an API call, mapping client errors to translated Home Assistant errors."""
    try:
        return await awaitable
    except PrintLogValidationError as err:
        raise _error("api_validation", message=str(err)) from err
    except PrintLogNotFoundError as err:
        raise _error("spool_not_found") from err
    except PrintLogUnsupportedError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_too_old") from err
    except PrintLogAuthError as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="api_auth") from err
    except (PrintLogConnectionError, PrintLogRateLimitError) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="api_unavailable"
        ) from err


async def _async_adjust_remaining(call: ServiceCall) -> dict[str, Any]:
    hass, data = call.hass, call.data
    amount = data[ATTR_AMOUNT]
    if not math.isfinite(amount) or amount == 0:
        raise _error("amount_invalid")
    entry, spool_id = _resolve_spool(hass, data)
    spool = entry.runtime_data.snapshot.data.spool_by_id(_normalized(spool_id))
    if spool is not None and spool.remaining_grams is None:
        raise _error("spool_untracked")
    result = await _call(
        entry.runtime_data.client.adjust_remaining(
            spool_id, UNIT_SOURCES[data[ATTR_UNIT]], amount, data.get(ATTR_NOTES)
        )
    )
    await entry.runtime_data.snapshot.async_request_refresh()
    return {
        "before_grams": result.get("beforeGrams"),
        "after_grams": result.get("afterGrams"),
    }


async def _async_set_spool_active(call: ServiceCall) -> dict[str, Any]:
    entry, spool_id = _resolve_spool(call.hass, call.data)
    await _call(entry.runtime_data.client.set_spool_active(spool_id, call.data[ATTR_ACTIVE]))
    await entry.runtime_data.snapshot.async_request_refresh()
    return {}


def _resolve_printer(
    hass: HomeAssistant, device_id: str, entry_id: str | None
) -> tuple[ConfigEntry, int]:
    """The entry and printer id of a printlog printer device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise _error("invalid_printer_device")
    for domain, ident in device.identifiers:
        if domain != DOMAIN:
            continue
        match = _PRINTER_IDENTIFIER.search(ident)
        if match is None:
            continue
        for candidate in device.config_entries:
            entry = hass.config_entries.async_get_entry(candidate)
            if entry is not None and entry.domain == DOMAIN:
                if entry_id and entry_id != entry.entry_id:
                    raise _error("device_entry_mismatch")
                return _entry_by_id(hass, entry.entry_id), int(match.group(1))
    raise _error("invalid_printer_device")


async def _async_log_maintenance(call: ServiceCall) -> dict[str, Any]:
    hass, data = call.hass, call.data
    if data.get(ATTR_MAINTENANCE_ID):
        raise _error("maintenance_id_unsupported")
    entry, printer_id = _resolve_printer(hass, data[ATTR_DEVICE_ID], data.get(ATTR_CONFIG_ENTRY_ID))
    date: datetime | None = data.get(ATTR_DATE)
    if date is None:
        date = dt_util.utcnow()
    elif date.tzinfo is None:
        tz = (
            await dt_util.async_get_time_zone(hass.config.time_zone)
            or dt_util.get_default_time_zone()
        )
        date = dt_util.as_utc(date.replace(tzinfo=tz))
    result = await _call(
        entry.runtime_data.client.create_maintenance(
            printer_id,
            date,
            data.get(ATTR_CATEGORY),
            data[ATTR_DESCRIPTION],
            data.get(ATTR_NOTES),
        )
    )
    await entry.runtime_data.snapshot.async_request_refresh()
    return {"maintenance_id": result.get("id")}


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the actions once, in async_setup."""
    for name, handler, schema in (
        (SERVICE_ADJUST_REMAINING, _async_adjust_remaining, ADJUST_SCHEMA),
        (SERVICE_SET_SPOOL_ACTIVE, _async_set_spool_active, SET_ACTIVE_SCHEMA),
        (SERVICE_LOG_MAINTENANCE, _async_log_maintenance, MAINTENANCE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
