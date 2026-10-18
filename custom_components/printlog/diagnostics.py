"""Diagnostics for the 3D Print Log integration."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY

TO_REDACT = {
    CONF_API_KEY,
    "title",
    "name",
    "display_name",
    "printer_name",
    "description",
    "notes",
    "storage_location",
    "url",
    "slot_label",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with names, titles, notes and URLs redacted."""
    data = entry.runtime_data
    snapshot = data.snapshot.data
    # A JSON round trip turns tuples into lists and datetimes into strings,
    # so the redaction below reaches every level.
    snapshot_dict = (
        json.loads(json.dumps(asdict(snapshot), default=str)) if snapshot is not None else None
    )
    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "coordinator": data.snapshot.diagnostics(),
            "slow": {"last_update_success": data.slow.last_update_success},
            "snapshot": snapshot_dict,
        },
        TO_REDACT,
    )
