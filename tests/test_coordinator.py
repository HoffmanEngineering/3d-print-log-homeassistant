"""Coordinator, runtime data, repair, event store and stale-device tests."""

from __future__ import annotations

import copy
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.printlog.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://api.3dprintlog.com"
UID = "api.3dprintlog.com:42"
VERSION_URL = f"{BASE}/api/sync/version"
SNAPSHOT_URL = f"{BASE}/api/sync/snapshot"
UNREAD_URL = f"{BASE}/api/Notifications/unread-count"
NOW = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
LOGGER_NAME = "custom_components.printlog"


def load_snapshot() -> dict:
    return json.loads((FIXTURES / "snapshot_full.json").read_text(encoding="utf-8"))


class FakeApi:
    """Programmable responses for the three polled endpoints."""

    def __init__(self, aioclient_mock) -> None:
        self.mock = aioclient_mock
        self.version = "v1"
        self.version_status = 200
        self.snapshot: dict = load_snapshot()
        self.snapshot["version"] = self.version
        self.snapshot_status = 200
        self.snapshot_etag = '"s1"'
        self.unread_status = 200
        self.retry_after: str | None = None
        aioclient_mock.get(VERSION_URL, side_effect=self._version)
        aioclient_mock.get(SNAPSHOT_URL, side_effect=self._snapshot)
        aioclient_mock.get(UNREAD_URL, side_effect=self._unread)

    def _resp(self, method, url, status, payload=None, headers=None):
        return AiohttpClientMockResponse(method, url, status=status, json=payload, headers=headers)

    def _error(self, method, url, status):
        headers = {"Retry-After": self.retry_after} if self.retry_after else None
        return self._resp(method, url, status, headers=headers)

    async def _version(self, method, url, data):
        if self.version_status != 200:
            return self._error(method, url, self.version_status)
        return self._resp(
            method, url, 200, {"version": self.version}, {"ETag": f'"{self.version}"'}
        )

    async def _snapshot(self, method, url, data):
        if self.snapshot_status != 200:
            return self._error(method, url, self.snapshot_status)
        return self._resp(method, url, 200, self.snapshot, {"ETag": self.snapshot_etag})

    async def _unread(self, method, url, data):
        if self.unread_status != 200:
            return self._error(method, url, self.unread_status)
        return self._resp(method, url, 200, {"count": 3})

    def calls(self, url: str) -> list[tuple]:
        return [c for c in self.mock.mock_calls if str(c[1]).split("?")[0] == url]

    def set_change(self, version: str, etag: str, mutate=None) -> None:
        """Bump the version and serve a changed snapshot."""
        self.version = version
        self.snapshot_etag = etag
        self.snapshot = copy.deepcopy(self.snapshot)
        self.snapshot["version"] = version
        if mutate:
            mutate(self.snapshot)


@pytest.fixture
def api(aioclient_mock) -> FakeApi:
    return FakeApi(aioclient_mock)


def make_entry(**kwargs) -> MockConfigEntry:
    defaults = {
        "domain": DOMAIN,
        "title": "Chris",
        "unique_id": UID,
        "version": 1,
        "minor_version": 1,
        "data": {"base_url": BASE, "auth_type": "api_key", "api_key": "secret-key"},
    }
    defaults.update(kwargs)
    return MockConfigEntry(**defaults)


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry | None = None):
    entry = entry or make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def finish_101(snapshot: dict) -> None:
    """Printer 101's current print 9001 finishes successfully."""
    printer = next(p for p in snapshot["printers"] if p["id"] == 101)
    current = printer.pop("currentPrint")
    printer["currentPrint"] = None
    printer["lastFinishedPrint"] = {
        "id": current["id"],
        "title": current["title"],
        "status": "success",
        "startedAt": current["startedAt"],
        "finishedAt": "2026-10-17T14:50:00+00:00",
        "source": current["source"],
        "url": current["url"],
    }


@pytest.fixture(autouse=True)
def _frozen_now(freezer):
    freezer.move_to(NOW)


# --- Setup, version and snapshot polling ---------------------------------------


async def test_first_refresh_fetches_snapshot(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data
    assert data.client is not None
    assert data.snapshot.entry_uid == UID
    assert data.snapshot.data.version == "v1"
    assert {p.id for p in data.snapshot.data.printers} == {101, 102}
    assert data.slow.data == {"count": 3}
    assert len(api.calls(VERSION_URL)) == 1
    assert len(api.calls(SNAPSHOT_URL)) == 1


async def test_version_304_skips_snapshot(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    api.version_status = 304
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert len(api.calls(VERSION_URL)) == 2
    assert len(api.calls(SNAPSHOT_URL)) == 1
    # The second version call sent the ETag from the first.
    assert api.calls(VERSION_URL)[1][3]["If-None-Match"] == '"v1"'


async def test_version_unchanged_200_skips_snapshot(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    await entry.runtime_data.snapshot.async_refresh()
    assert len(api.calls(SNAPSHOT_URL)) == 1


async def test_version_change_fetches_snapshot(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    api.set_change("v2", '"s2"')
    await coordinator.async_refresh()
    calls = api.calls(SNAPSHOT_URL)
    assert len(calls) == 2
    assert calls[1][3]["If-None-Match"] == '"s1"'
    assert coordinator.data.version == "v2"


async def test_forced_snapshot_after_300s_even_if_version_304(
    hass: HomeAssistant, api: FakeApi, freezer
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    api.version_status = 304
    freezer.tick(timedelta(seconds=299))
    await coordinator.async_refresh()
    assert len(api.calls(SNAPSHOT_URL)) == 1
    freezer.tick(timedelta(seconds=2))
    await coordinator.async_refresh()
    assert len(api.calls(SNAPSHOT_URL)) == 2


async def test_snapshot_304_keeps_data_no_listener_update(
    hass: HomeAssistant, api: FakeApi
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    before = coordinator.data
    updates: list[None] = []
    unsub = coordinator.async_add_listener(lambda: updates.append(None))
    api.version = "v2"
    api.snapshot_status = 304
    await coordinator.async_refresh()
    unsub()
    assert len(api.calls(SNAPSHOT_URL)) == 2
    assert coordinator.last_update_success
    assert coordinator.data is before
    assert updates == []


async def test_time_zone_from_hass_config(hass: HomeAssistant, api: FakeApi) -> None:
    await hass.config.async_update(time_zone="America/Chicago")
    await setup_entry(hass)
    url = api.calls(SNAPSHOT_URL)[0][1]
    assert url.query["timeZone"] == "America/Chicago"


# --- Errors --------------------------------------------------------------------


async def test_schema_version_2_raises_repair_and_update_failed(
    hass: HomeAssistant, api: FakeApi
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    registry = ir.async_get(hass)

    api.set_change("v2", '"s2"', lambda s: s.update(schemaVersion=2))
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    issue = registry.async_get_issue(DOMAIN, f"unsupported_schema_{entry.entry_id}")
    assert issue is not None
    assert issue.translation_key == "unsupported_schema"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert not issue.is_fixable
    # The previous data is kept.
    assert coordinator.data.version == "v1"

    api.set_change("v3", '"s3"', lambda s: s.update(schemaVersion=1))
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.version == "v3"
    assert registry.async_get_issue(DOMAIN, f"unsupported_schema_{entry.entry_id}") is None


async def test_invalid_snapshot_update_failed(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    api.set_change("v2", '"s2"', lambda s: s.pop("account"))
    await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_401_starts_reauth(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    api.version_status = 401
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_401_on_setup_starts_reauth(hass: HomeAssistant, api: FakeApi) -> None:
    api.version_status = 401
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_connection_failure_on_setup_retries(hass: HomeAssistant, api: FakeApi) -> None:
    api.version_status = 503
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_429_defers_next_refresh(hass: HomeAssistant, api: FakeApi, freezer) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    unsub = coordinator.async_add_listener(lambda: None)
    try:
        api.version_status = 429
        api.retry_after = "120"
        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        assert len(api.calls(VERSION_URL)) == 2

        api.version_status = 200
        api.retry_after = None
        await tick(hass, freezer, 60)
        assert len(api.calls(VERSION_URL)) == 2  # the normal 30 s interval was skipped
        await tick(hass, freezer, 61)
        assert len(api.calls(VERSION_URL)) == 3
        assert coordinator.last_update_success
        # The interval is back to normal afterwards.
        await tick(hass, freezer, 31)
        assert len(api.calls(VERSION_URL)) == 4
    finally:
        unsub()


async def test_429_short_retry_after_uses_interval(
    hass: HomeAssistant, api: FakeApi, freezer
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    unsub = coordinator.async_add_listener(lambda: None)
    try:
        api.version_status = 429
        api.retry_after = "2"
        await coordinator.async_refresh()
        api.version_status = 200
        await tick(hass, freezer, 10)
        assert len(api.calls(VERSION_URL)) == 2
        await tick(hass, freezer, 21)
        assert len(api.calls(VERSION_URL)) == 3
    finally:
        unsub()


async def test_connection_error_unavailable_logs_once(
    hass: HomeAssistant, api: FakeApi, caplog
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    caplog.clear()
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    api.version_status = 503
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    problems = [
        r for r in caplog.records if r.name.startswith(LOGGER_NAME) and r.levelno >= logging.WARNING
    ]
    assert len(problems) == 1
    assert "secret-key" not in caplog.text

    caplog.clear()
    api.version_status = 200
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    infos = [
        r for r in caplog.records if r.name.startswith(LOGGER_NAME) and r.levelno == logging.INFO
    ]
    assert len(infos) == 1
    assert "recovered" in infos[0].getMessage()


async def test_slow_failure_isolated(hass: HomeAssistant, api: FakeApi) -> None:
    api.unread_status = 503
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data
    assert not data.slow.last_update_success
    assert data.snapshot.last_update_success
    await data.snapshot.async_refresh()
    assert data.snapshot.last_update_success

    api.unread_status = 200
    await data.slow.async_refresh()
    assert data.slow.last_update_success
    assert data.slow.data == {"count": 3}


# --- Stale devices -------------------------------------------------------------


async def test_stale_printer_device_removed(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    registry = dr.async_get(hass)
    ids = {
        name: registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{UID}:{name}")}
        ).id
        for name in ("printer:101", "printer:102", "account")
    }

    def drop_102(s: dict) -> None:
        s["printers"] = [p for p in s["printers"] if p["id"] != 102]

    api.set_change("v2", '"s2"', drop_102)
    await entry.runtime_data.snapshot.async_refresh()

    assert registry.async_get(ids["printer:101"]) is not None
    assert registry.async_get(ids["printer:102"]) is None
    assert registry.async_get(ids["account"]) is not None


async def test_device_of_other_entry_not_removed(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    other = make_entry(unique_id="other:1")
    other.add_to_hass(hass)
    registry = dr.async_get(hass)
    other_dev = registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, "other:1:printer:555")}
    )
    await entry.runtime_data.snapshot.async_refresh()
    api.set_change("v2", '"s2"')
    await entry.runtime_data.snapshot.async_refresh()
    assert registry.async_get(other_dev.id) is not None


# --- Events and the store ------------------------------------------------------


def store_key(entry) -> str:
    return f"printlog.{entry.entry_id}.events"


async def test_events_dispatched_and_store_saved(
    hass: HomeAssistant, api: FakeApi, freezer, hass_storage: dict[str, Any]
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    received: list[list] = []
    unsub = coordinator.async_add_event_listener(received.append)

    # A version bump with no print change fires nothing.
    api.set_change("v2", '"s2"')
    await coordinator.async_refresh()
    assert received == []

    api.set_change("v3", '"s3"', finish_101)
    await coordinator.async_refresh()
    assert len(received) == 1
    (event,) = received[0]
    assert (event.printer_id, event.print_id, event.status) == (101, 9001, "success")

    await tick(hass, freezer, 11)
    saved = hass_storage[store_key(entry)]
    assert saved["version"] == 1
    assert saved["data"]["printers"]["101"]["seen"] == [9001]
    assert saved["data"]["printers"]["101"]["current"] is None

    unsub()
    api.set_change("v4", '"s4"')
    await coordinator.async_refresh()
    assert len(received) == 1


async def test_no_events_on_304_or_error(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    received: list[list] = []
    coordinator.async_add_event_listener(received.append)
    api.version = "v2"
    api.snapshot_status = 304
    await coordinator.async_refresh()
    api.snapshot_status = 503
    await coordinator.async_refresh()
    assert received == []


async def test_failing_event_listener_does_not_break_update(
    hass: HomeAssistant, api: FakeApi
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    received: list[list] = []

    def boom(events):
        raise RuntimeError("listener bug")

    coordinator.async_add_event_listener(boom)
    coordinator.async_add_event_listener(received.append)
    api.set_change("v2", '"s2"', finish_101)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert len(received) == 1


async def test_finish_while_down_fires_once_after_restart(
    hass: HomeAssistant, api: FakeApi, hass_storage: dict[str, Any]
) -> None:
    """R7: state loaded from the store keys printers by int, so a finish fires once."""
    entry = make_entry()
    hass_storage[store_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": store_key(entry),
        # Written by an earlier run: string keys, and no "started" list.
        "data": {"printers": {"101": {"seen": [], "current": 9001}}},
    }
    api.set_change("v2", '"s2"', finish_101)
    received: list[list] = []
    real_forward = hass.config_entries.async_forward_entry_setups

    async def forward_and_listen(config_entry, platforms):
        # Stands in for the event platform, which registers during forwarding.
        config_entry.runtime_data.snapshot.async_add_event_listener(received.append)
        return await real_forward(config_entry, platforms)

    with patch.object(
        hass.config_entries, "async_forward_entry_setups", side_effect=forward_and_listen
    ):
        await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert len(received) == 1
    (event,) = received[0]
    assert (event.print_id, event.status) == (9001, "success")

    # Reloading again from the now-current state fires nothing new.
    await entry.runtime_data.snapshot.async_refresh()
    assert len(received) == 1


async def test_reload_before_delayed_save_keeps_state(
    hass: HomeAssistant, api: FakeApi, hass_storage: dict[str, Any]
) -> None:
    """Unload writes the pending state, so a reload doesn't fire the finish again."""
    entry = await setup_entry(hass)
    received: list[list] = []
    entry.runtime_data.snapshot.async_add_event_listener(received.append)
    api.set_change("v2", '"s2"', finish_101)
    await entry.runtime_data.snapshot.async_refresh()
    assert len(received) == 1

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert 9001 in entry.runtime_data.snapshot.event_state[101].seen
    assert hass_storage[store_key(entry)]["data"]["printers"]["101"]["seen"] == [9001]


async def test_store_removed_before_delayed_save(
    hass: HomeAssistant, api: FakeApi, freezer, hass_storage: dict[str, Any]
) -> None:
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 11)
    assert store_key(entry) not in hass_storage


async def test_store_removed_with_entry(
    hass: HomeAssistant, api: FakeApi, freezer, hass_storage: dict[str, Any]
) -> None:
    entry = await setup_entry(hass)
    await tick(hass, freezer, 11)
    assert store_key(entry) in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert store_key(entry) not in hass_storage


# --- Entry lifecycle and diagnostics -------------------------------------------


async def test_unload(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_options_change_reloads_entry(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    assert entry.runtime_data.snapshot.update_interval == timedelta(seconds=30)
    hass.config_entries.async_update_entry(entry, options={"scan_interval": 60})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.snapshot.update_interval == timedelta(seconds=60)
    assert entry.runtime_data.slow.update_interval == timedelta(seconds=300)


async def test_diagnostics_has_no_secrets(hass: HomeAssistant, api: FakeApi) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    api.version_status = 503
    await coordinator.async_refresh()
    diag = coordinator.diagnostics()
    assert diag["version_etag"] == '"v1"'
    assert diag["snapshot_etag"] == '"s1"'
    assert diag["last_snapshot_fetch"] == NOW.isoformat()
    assert "PrintLogConnectionError" in diag["last_error"]
    assert diag["event_state"]["101"]["current"] == 9001
    assert "secret-key" not in json.dumps(diag)
