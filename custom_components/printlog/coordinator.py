"""Coordinators and runtime data for the 3D Print Log integration (spec 4.5)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    NOT_MODIFIED,
    PrintLogAuthError,
    PrintLogClient,
    PrintLogError,
    PrintLogRateLimitError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FORCED_SNAPSHOT_SECONDS,
    SLOW_INTERVAL_SECONDS,
    SUPPORTED_SCHEMA_VERSION,
)
from .events import PrinterEventState, PrintEvent, diff
from .models import Snapshot
from .repairs import async_clear_unsupported_schema, async_raise_unsupported_schema
from .store import EventStore

_LOGGER = logging.getLogger(__package__)

type EventListener = Callable[[list[PrintEvent]], None]


def _translate_error(err: PrintLogError, interval: timedelta) -> Exception:
    """Map a client error to the exception the coordinator expects."""
    if isinstance(err, PrintLogAuthError):
        return ConfigEntryAuthFailed("The API key was rejected")
    if isinstance(err, PrintLogRateLimitError):
        # HA honors retry_after for exactly one cycle, then goes back to the
        # interval. Never retry sooner than the normal interval.
        retry_after = max(interval.total_seconds(), err.retry_after or 0)
        return UpdateFailed("Rate limited by 3D Print Log", retry_after=retry_after)
    return UpdateFailed(f"Error communicating with 3D Print Log: {type(err).__name__}: {err}")


class SnapshotCoordinator(DataUpdateCoordinator[Snapshot]):
    """Polls the sync version and fetches the snapshot when it changes."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PrintLogClient,
        store: EventStore,
        event_state: dict[int, PrinterEventState],
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="3D Print Log snapshot",
            update_interval=timedelta(seconds=interval),
            always_update=False,
        )
        self.entry_uid: str = entry.unique_id or entry.entry_id
        self._client = client
        self._store = store
        self._event_state = event_state
        self._event_listeners: list[EventListener] = []
        self._dispatch_ready = False
        self._pending_events: list[PrintEvent] = []
        self._state_unsaved = False
        # Printer device identifier -> the snapshot key (ETag) in which it was
        # first found missing. A device is removed on the second strike.
        self._missing_since: dict[str, str] = {}

        self._version: str | None = None
        self._version_etag: str | None = None
        self._snapshot_etag: str | None = None
        self._last_snapshot_monotonic: float | None = None
        self._last_snapshot_fetch: datetime | None = None
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None
        # (version, monotonic time) of the last snapshot with an unsupported schema.
        self._unsupported: tuple[str | None, float] | None = None
        # The account's time zone, resolved off the event loop whenever the
        # snapshot names a new one. None when the name is unknown.
        self._account_zone_name: str | None = None
        self._account_zone: tzinfo | None = None

    # --- Polling ---------------------------------------------------------------

    async def _async_update_data(self) -> Snapshot:
        try:
            return await self._async_poll()
        except PrintLogError as err:
            self._record_error(f"{type(err).__name__}: {err}")
            raise _translate_error(err, self.update_interval) from err
        except UpdateFailed as err:
            self._record_error(str(err))
            raise

    def _record_error(self, message: str) -> None:
        self._last_error = message
        self._last_error_at = dt_util.utcnow()

    async def _async_poll(self) -> Snapshot:
        new_version: tuple[str, str | None] | None = None
        result = await self._client.get_version(self._version_etag)
        if result is not NOT_MODIFIED:
            version, etag = result
            if version != self._version:
                # Committed only once the snapshot has been fetched, so a failed
                # snapshot fetch is retried on the next tick.
                new_version = (version, etag)
            else:
                self._version_etag = etag

        candidate = new_version[0] if new_version else self._version
        if self._unsupported is not None:
            unsupported_version, seen_at = self._unsupported
            if (
                candidate == unsupported_version
                and time.monotonic() - seen_at < FORCED_SNAPSHOT_SECONDS
            ):
                # Same snapshot as last time: don't download it again.
                raise UpdateFailed("The snapshot schema version is still not supported")

        if not self._snapshot_due(new_version is not None):
            return self.data

        fetched = await self._client.get_snapshot(
            str(self.hass.config.time_zone), self._snapshot_etag
        )
        if fetched is NOT_MODIFIED:
            if self.data is None:
                raise UpdateFailed("The snapshot was not modified, but none is cached")
            self._commit(new_version, self._snapshot_etag)
            return self.data

        raw, snapshot_etag = fetched
        snapshot = self._parse(raw, candidate)
        await self._async_resolve_account_zone(snapshot.account.time_zone)
        self._commit(new_version, snapshot_etag)
        self._process_events(snapshot)
        self._remove_stale_devices(snapshot, snapshot_etag or f"version:{snapshot.version}")
        return snapshot

    async def _async_resolve_account_zone(self, name: str) -> None:
        if name == self._account_zone_name:
            return
        self._account_zone = await dt_util.async_get_time_zone(name) if name else None
        self._account_zone_name = name

    @property
    def account_zone(self) -> tzinfo:
        """The account's time zone, or Home Assistant's when it is unknown. No I/O."""
        return self._account_zone or dt_util.get_default_time_zone()

    def _snapshot_due(self, version_changed: bool) -> bool:
        if self.data is None or version_changed or self._last_snapshot_monotonic is None:
            return True
        return time.monotonic() - self._last_snapshot_monotonic >= FORCED_SNAPSHOT_SECONDS

    def _parse(self, raw: dict, version: str | None) -> Snapshot:
        schema_version = raw.get("schemaVersion")
        if not isinstance(schema_version, int):
            raise UpdateFailed("The snapshot has no schema version")
        if schema_version > SUPPORTED_SCHEMA_VERSION:
            self._unsupported = (version, time.monotonic())
            async_raise_unsupported_schema(self.hass, self.config_entry, schema_version)
            raise UpdateFailed(
                f"Snapshot schema version {schema_version} is not supported; update the integration"
            )
        try:
            snapshot = Snapshot.from_json(raw)
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise UpdateFailed(f"Invalid snapshot: {type(err).__name__}") from err
        self._unsupported = None
        async_clear_unsupported_schema(self.hass, self.config_entry)
        return snapshot

    def _commit(
        self, new_version: tuple[str, str | None] | None, snapshot_etag: str | None
    ) -> None:
        if new_version is not None:
            self._version, self._version_etag = new_version
        self._snapshot_etag = snapshot_etag
        self._last_snapshot_monotonic = time.monotonic()
        self._last_snapshot_fetch = dt_util.utcnow()

    # --- Events ----------------------------------------------------------------

    def _process_events(self, snapshot: Snapshot) -> None:
        if self._store.closed:
            # The entry is unloading; the next setup diffs from the saved state.
            return
        events, state = diff(self._event_state, snapshot, dt_util.utcnow())
        if state != self._event_state:
            self._event_state = state
            if self._dispatch_ready:
                self._store.async_save_delayed(state)
            else:
                # Not persisted until dispatch starts, so events that were never
                # delivered (setup failed) are found again next time.
                self._state_unsaved = True
        if not events:
            return
        if self._dispatch_ready:
            self._dispatch(events)
        else:
            self._pending_events.extend(events)

    def _dispatch(self, events: list[PrintEvent]) -> None:
        for listener in list(self._event_listeners):
            try:
                listener(events)
            except Exception:
                _LOGGER.exception("Error in a print event listener")

    @callback
    def async_add_event_listener(self, cb: EventListener) -> Callable[[], None]:
        """Call `cb` with each non-empty list of print events. Returns a remover."""
        self._event_listeners.append(cb)

        @callback
        def remove() -> None:
            if cb in self._event_listeners:
                self._event_listeners.remove(cb)

        return remove

    @callback
    def async_start_dispatch(self) -> None:
        """Start delivering events, including any found during setup.

        Setup calls this after the platforms are forwarded, so listeners that
        platforms register see the events of the first refresh (for example a
        print that finished while Home Assistant was down).
        """
        self._dispatch_ready = True
        pending, self._pending_events = self._pending_events, []
        if self._state_unsaved:
            self._state_unsaved = False
            self._store.async_save_delayed(self._event_state)
        if pending:
            self._dispatch(pending)

    @callback
    def async_discard_pending_events(self) -> None:
        """Drop queued events without saving the state that produced them."""
        self._pending_events = []
        self._state_unsaved = False

    @property
    def event_state(self) -> dict[int, PrinterEventState]:
        return self._event_state

    # --- Devices ---------------------------------------------------------------

    def _remove_stale_devices(self, snapshot: Snapshot, snapshot_key: str) -> None:
        """Remove printer devices missing from two consecutive, different snapshots.

        One bad response (an empty or truncated printer list) must not delete
        devices and the user's customizations with them.
        """
        prefix = f"{self.entry_uid}:printer:"
        current = {f"{prefix}{p.id}" for p in snapshot.printers}
        registry = dr.async_get(self.hass)
        missing_now: dict[str, str] = {}
        for device in dr.async_entries_for_config_entry(registry, self.config_entry.entry_id):
            printer_ids = {
                value
                for domain, value in device.identifiers
                if domain == DOMAIN and value.startswith(prefix)
            }
            if not printer_ids or printer_ids & current:
                continue
            ident = min(printer_ids)
            first_missing = self._missing_since.get(ident)
            if first_missing is not None and first_missing != snapshot_key:
                registry.async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )
            else:
                missing_now[ident] = first_missing or snapshot_key
        # Printers that reappeared (or whose devices are gone) start over.
        self._missing_since = missing_now

    # --- Diagnostics -----------------------------------------------------------

    def diagnostics(self) -> dict:
        """Coordinator state for diagnostics. Contains no secrets."""
        return {
            "version": self._version,
            "version_etag": self._version_etag,
            "snapshot_etag": self._snapshot_etag,
            "last_snapshot_fetch": (
                self._last_snapshot_fetch.isoformat() if self._last_snapshot_fetch else None
            ),
            "last_update_success": self.last_update_success,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at.isoformat() if self._last_error_at else None,
            "update_interval_seconds": (
                self.update_interval.total_seconds() if self.update_interval else None
            ),
            "event_state": {str(pid): s.to_dict() for pid, s in self._event_state.items()},
        }


class SlowCoordinator(DataUpdateCoordinator[dict]):
    """Polls the unread notification count. Its failures affect only its own entities."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: PrintLogClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="3D Print Log notifications",
            update_interval=timedelta(seconds=SLOW_INTERVAL_SECONDS),
        )
        self._client = client

    async def _async_update_data(self) -> dict:
        try:
            return await self._client.get_unread_count()
        except PrintLogError as err:
            raise _translate_error(err, self.update_interval) from err


@dataclass
class PrintLogData:
    """Runtime data stored on the config entry."""

    client: PrintLogClient
    snapshot: SnapshotCoordinator
    slow: SlowCoordinator
    store: EventStore
