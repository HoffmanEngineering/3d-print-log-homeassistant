"""Repair issues for the 3D Print Log integration (no fixable flows in v1)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, ISSUE_UNSUPPORTED_SCHEMA, SUPPORTED_SCHEMA_VERSION


def _issue_id(entry: ConfigEntry) -> str:
    return f"{ISSUE_UNSUPPORTED_SCHEMA}_{entry.entry_id}"


def async_raise_unsupported_schema(
    hass: HomeAssistant, entry: ConfigEntry, schema_version: int
) -> None:
    """The API sends a snapshot schema this version of the integration can't read."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UNSUPPORTED_SCHEMA,
        translation_placeholders={
            "title": entry.title,
            "schema_version": str(schema_version),
            "supported_version": str(SUPPORTED_SCHEMA_VERSION),
        },
    )


def async_clear_unsupported_schema(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the issue (no-op when it doesn't exist)."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))
