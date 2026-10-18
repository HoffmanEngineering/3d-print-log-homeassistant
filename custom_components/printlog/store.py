"""Persisted event state (spec 6.1)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_MINOR_VERSION, STORAGE_VERSION, STORE_SAVE_DELAY
from .events import PrinterEventState

_LOGGER = logging.getLogger(__name__)


class _EventsStore(Store[dict[str, Any]]):
    """Store with a migrate function, so later versions can change the format."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        # Only version 1.1 exists. A newer minor version stays readable; a
        # newer major version is refused by Store itself.
        return old_data


class EventStore:
    """Load and save the per-printer event state of one config entry.

    JSON object keys are strings, so printer ids are written as strings and
    converted back to int on load.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = _EventsStore(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.events",
            minor_version=STORAGE_MINOR_VERSION,
        )
        self._data: dict[str, Any] = {"printers": {}}
        self._dirty = False

    async def async_load(self) -> dict[int, PrinterEventState]:
        stored = await self._store.async_load()
        state: dict[int, PrinterEventState] = {}
        printers = stored.get("printers") if isinstance(stored, dict) else None
        for key, value in (printers or {}).items():
            try:
                state[int(key)] = PrinterEventState.from_dict(value)
            except TypeError, ValueError:
                _LOGGER.debug("Ignoring invalid stored event state for printer %r", key)
        return state

    def async_save_delayed(self, state: dict[int, PrinterEventState]) -> None:
        self._data = {"printers": {str(pid): s.to_dict() for pid, s in state.items()}}
        self._dirty = True
        self._store.async_delay_save(self._data_to_save, STORE_SAVE_DELAY)

    def _data_to_save(self) -> dict[str, Any]:
        self._dirty = False
        return self._data

    async def async_flush(self) -> None:
        """Write a pending delayed save now (on unload, so a reload reads it)."""
        if self._dirty:
            self._dirty = False
            # async_save also cancels the pending delayed write.
            await self._store.async_save(self._data)

    async def async_remove(self) -> None:
        await self._store.async_remove()
