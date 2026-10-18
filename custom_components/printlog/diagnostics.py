"""Diagnostics for the 3D Print Log integration."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_BASE_URL, DEFAULT_BASE_URL

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
    "category",
    "brand",
    "color_name",
}
# make, model, material and time_zone stay visible on purpose: they are
# catalog-like values that help with support.


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
    entry_data = dict(entry.data)
    if entry_data.get(CONF_BASE_URL) != DEFAULT_BASE_URL:
        # A self-hosted hostname can identify the user.
        entry_data[CONF_BASE_URL] = "**REDACTED**"
    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "data": entry_data,
                "options": dict(entry.options),
            },
            "coordinator": data.snapshot.diagnostics(),
            "slow": {"last_update_success": data.slow.last_update_success},
            "snapshot": snapshot_dict,
        },
        TO_REDACT,
    )
