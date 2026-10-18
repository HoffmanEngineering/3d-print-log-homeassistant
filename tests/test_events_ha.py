"""Event entity and bus event tests (spec 5.2, 6.3)."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.printlog.const import DOMAIN
from custom_components.printlog.events import PrintEvent

from .test_coordinator import (  # noqa: F401
    NOW,
    UID,
    FakeApi,
    api,
    finish_101,
    make_entry,
    setup_entry,
    store_key,
    tick,
)
from .test_sensor import entity_id

STARTED = "printlog_print_started"
FINISHED = "printlog_print_finished"
FAILED = "printlog_print_failed"


@pytest.fixture(autouse=True)
def _frozen_now(freezer):
    freezer.move_to(NOW)


def start_102(snapshot: dict) -> None:
    """Printer 102 starts print 9100 (10 minutes ago)."""
    printer = next(p for p in snapshot["printers"] if p["id"] == 102)
    printer["currentPrint"] = {
        "id": 9100,
        "title": "Vase",
        "startedAt": "2026-10-17T14:50:00+00:00",
        "source": "manual",
        "url": "https://3dprintlog.com/prints/9100",
        "filament": [],
    }


def finish_with(status: str):
    def mutate(snapshot: dict) -> None:
        finish_101(snapshot)
        printer = next(p for p in snapshot["printers"] if p["id"] == 101)
        printer["lastFinishedPrint"]["status"] = status

    return mutate


def printer_device_id(hass: HomeAssistant, config_entry_id: str, printer_id: int) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{UID}:printer:{printer_id}"), config_entry_id
    )
    assert device is not None
    return device.id


async def test_bus_event_payload(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    entry = await setup_entry(hass)
    started = async_capture_events(hass, STARTED)
    finished = async_capture_events(hass, FINISHED)
    coordinator = entry.runtime_data.snapshot

    api.set_change("v2", '"s2"', start_102)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert [e.data for e in started] == [
        {
            "version": 1,
            "entry_id": entry.entry_id,
            "device_id": printer_device_id(hass, entry.entry_id, 102),
            "printer_id": 102,
            "printer_name": "Printer 102",
            "print_id": 9100,
            "status": "started",
            "title": "Vase",
            "started_at": "2026-10-17T14:50:00+00:00",
            "finished_at": None,
            "source": "manual",
            "url": "https://3dprintlog.com/prints/9100",
        }
    ]

    api.set_change("v3", '"s3"', finish_101)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert [e.data for e in finished] == [
        {
            "version": 1,
            "entry_id": entry.entry_id,
            "device_id": printer_device_id(hass, entry.entry_id, 101),
            "printer_id": 101,
            "printer_name": "Snapmaker U1",
            "print_id": 9001,
            "status": "success",
            "title": "Benchy",
            "started_at": "2026-10-17T14:00:00+00:00",
            "finished_at": "2026-10-17T14:50:00+00:00",
            "source": "moonraker",
            "url": "https://3dprintlog.com/prints/9001",
        }
    ]
    assert len(started) == 1


@pytest.mark.parametrize("status", ["failed", "canceled", "partial_success"])
async def test_failed_statuses_map_to_failed_event(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    status: str,
) -> None:
    entry = await setup_entry(hass)
    failed = async_capture_events(hass, FAILED)
    finished = async_capture_events(hass, FINISHED)
    api.set_change("v2", '"s2"', finish_with(status))
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    assert finished == []
    assert len(failed) == 1
    assert failed[0].data["status"] == status


async def test_event_entity_types_and_data(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    entry = await setup_entry(hass)
    eid = entity_id(hass, "event", "printer:101:print")
    state = hass.states.get(eid)
    assert state.state == "unknown"
    assert state.attributes["event_types"] == [
        "started",
        "success",
        "failed",
        "canceled",
        "partial_success",
    ]
    assert hass.states.get(entity_id(hass, "event", "printer:102:print")).state == "unknown"

    api.set_change("v2", '"s2"', finish_with("canceled"))
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(eid)
    assert state.state != "unknown"
    assert state.attributes["event_type"] == "canceled"
    assert state.attributes["status"] == "canceled"
    assert state.attributes["print_id"] == 9001
    assert state.attributes["version"] == 1
    assert state.attributes["entry_id"] == entry.entry_id
    assert state.attributes["finished_at"] == "2026-10-17T14:50:00+00:00"
    assert "device_id" not in state.attributes
    # The other printer's entity is untouched.
    assert hass.states.get(entity_id(hass, "event", "printer:102:print")).state == "unknown"


async def test_restart_no_duplicate_started(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    freezer,
    hass_storage: dict[str, Any],
) -> None:
    entry = await setup_entry(hass)
    started = async_capture_events(hass, STARTED)
    api.set_change("v2", '"s2"', start_102)
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    assert len(started) == 1
    await tick(hass, freezer, 11)
    eid = entity_id(hass, "event", "printer:102:print")
    before = hass.states.get(eid)
    assert before.attributes["event_type"] == "started"
    assert 9100 in hass_storage[store_key(entry)]["data"]["printers"]["102"]["started"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert len(started) == 1
    # The entity restores its last event; nothing new fired.
    after = hass.states.get(eid)
    assert after.state == before.state
    assert after.attributes["print_id"] == 9100


async def test_finish_while_down_within_window_fires_once(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    hass_storage: dict[str, Any],
) -> None:
    entry = make_entry()
    hass_storage[store_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": store_key(entry),
        "data": {"printers": {"101": {"seen": [], "started": [9001], "current": 9001}}},
    }
    api.set_change("v2", '"s2"', finish_101)
    finished = async_capture_events(hass, FINISHED)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert len(finished) == 1
    assert finished[0].data["print_id"] == 9001
    assert finished[0].data["device_id"] == printer_device_id(hass, entry.entry_id, 101)
    state = hass.states.get(entity_id(hass, "event", "printer:101:print"))
    assert state.attributes["event_type"] == "success"
    assert state.attributes["print_id"] == 9001

    # Reloading from the persisted state does not fire it again.
    await entry.runtime_data.store.async_flush()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(finished) == 1


async def test_unknown_device_gives_null_device_id(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    entry = await setup_entry(hass)
    registry = dr.async_get(hass)
    registry.async_remove_device(printer_device_id(hass, entry.entry_id, 102))
    started = async_capture_events(hass, STARTED)
    api.set_change("v2", '"s2"', start_102)
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    assert started[0].data["device_id"] is None


def finish_and_start_101(snapshot: dict) -> None:
    finish_101(snapshot)
    printer = next(p for p in snapshot["printers"] if p["id"] == 101)
    printer["currentPrint"] = {
        "id": 9200,
        "title": "Cube",
        "startedAt": "2026-10-17T14:55:00+00:00",
        "source": "moonraker",
        "url": "https://3dprintlog.com/prints/9200",
        "filament": [],
    }


async def test_finish_and_start_in_one_pass_both_written(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    entry = await setup_entry(hass)
    eid = entity_id(hass, "event", "printer:101:print")
    changes = async_capture_events(hass, "state_changed")
    api.set_change("v2", '"s2"', finish_and_start_101)
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    types = [
        e.data["new_state"].attributes["event_type"]
        for e in changes
        if e.data["entity_id"] == eid and "event_type" in e.data["new_state"].attributes
    ]
    assert types == ["success", "started"]
    assert hass.states.get(eid).attributes["event_type"] == "started"


async def test_bad_event_does_not_abort_batch(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot
    started = async_capture_events(hass, STARTED)
    good = PrintEvent(102, "P", "started", 1, "t", None, None, None, None)
    bad = PrintEvent(102, "P", "bogus", 2, "t", None, None, None, None)
    coordinator._dispatch([bad, good])
    await hass.async_block_till_done()
    assert [e.data["print_id"] for e in started] == [1]
    state = hass.states.get(entity_id(hass, "event", "printer:102:print"))
    assert state.attributes["print_id"] == 1


async def test_bus_listener_removed_on_unload(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    entry = await setup_entry(hass)
    started = async_capture_events(hass, STARTED)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    api.set_change("v2", '"s2"', start_102)
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    assert len(started) == 1
