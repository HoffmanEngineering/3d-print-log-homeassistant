"""Binary sensor tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from .test_coordinator import NOW, FakeApi, api, setup_entry  # noqa: F401
from .test_sensor import entity_id, printer_of


@pytest.fixture(autouse=True)
def _frozen_now(freezer):
    freezer.move_to(NOW)


def bstate(hass: HomeAssistant, suffix: str):
    return hass.states.get(entity_id(hass, "binary_sensor", suffix))


async def test_binary_sensors_full_snapshot(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    with patch("custom_components.printlog.PLATFORMS", [Platform.BINARY_SENSOR]):
        entry = await setup_entry(hass)
    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


async def test_binary_sensor_states(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    connection = bstate(hass, "printer:101:connection")
    assert connection.state == "on"
    assert connection.attributes["kind"] == "moonraker"
    assert connection.attributes["device_class"] == "connectivity"
    assert bstate(hass, "printer:101:printing").state == "on"
    assert bstate(hass, "printer:102:printing").state == "off"
    due = bstate(hass, "printer:101:maintenance_due")
    assert due.state == "off"
    assert due.attributes["open_count"] == 2
    assert due.attributes["next_date"] == "2026-11-01T00:00:00+00:00"
    # Slot 1: 742.5 g left, 18.5 g needed.
    short = bstate(hass, "printer:101:slot:1:filament_short")
    assert short.state == "off"
    assert short.attributes["estimated_grams"] == 18.5
    assert short.attributes["remaining_grams"] == 742.5
    # Slot 2 has no estimate in the print.
    assert bstate(hass, "printer:101:slot:2:filament_short").state == "unknown"


async def test_connection_unknown_without_connector(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    await setup_entry(hass)
    state = bstate(hass, "printer:102:connection")
    assert state.state == "unknown"
    assert "kind" not in state.attributes


async def test_connection_off_when_stale(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    printer_of(api.snapshot, 101)["connection"]["status"] = "stale"
    await setup_entry(hass)
    assert bstate(hass, "printer:101:connection").state == "off"


@pytest.mark.parametrize(
    ("remaining", "estimate", "print_running", "expected"),
    [
        (10.0, 20.0, True, "on"),
        (30.0, 20.0, True, "off"),
        (20.0, 20.0, True, "off"),
        (10.0, 20.0, False, "unknown"),
        (10.0, None, True, "unknown"),
        (None, 20.0, True, "unknown"),
    ],
)
async def test_filament_short_states(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    remaining,
    estimate,
    print_running,
    expected,
) -> None:
    printer = printer_of(api.snapshot, 101)
    printer["loaded"][0]["remainingGrams"] = remaining
    printer["currentPrint"]["filament"][0]["estimatedGrams"] = estimate
    if not print_running:
        printer["currentPrint"] = None
    await setup_entry(hass)
    assert bstate(hass, "printer:101:slot:1:filament_short").state == expected


@pytest.mark.parametrize("api_slot", [0, None])
async def test_single_slot_spool_at_api_slot_zero_or_null(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    api_slot,
) -> None:
    """Ruling R11: API slot 0 (and null on a single-slot printer) is HA Slot 1."""
    printer = printer_of(api.snapshot, 102)
    printer["loaded"][0]["slot"] = api_slot
    printer["loaded"][0]["slotLabel"] = "T0" if api_slot == 0 else None
    printer["currentPrint"] = {
        "id": 9100,
        "title": "Clip",
        "filament": [
            {
                "slot": api_slot,
                "spoolId": printer["loaded"][0]["spoolId"],
                "estimatedGrams": 400.0,
            }
        ],
    }
    await setup_entry(hass)
    assert hass.states.get(entity_id(hass, "sensor", "printer:102:slot:1:filament")).state == (
        "Signal Red"
    )
    assert hass.states.get(entity_id(hass, "sensor", "printer:102:slot:1:remaining")).state == (
        "310.0"
    )
    short = bstate(hass, "printer:102:slot:1:filament_short")
    assert short.state == "on"
    assert short.attributes["estimated_grams"] == 400.0
    assert short.attributes["remaining_grams"] == 310.0


async def test_multi_slot_api_slots_zero_to_three(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    """Ruling R11: API slots 0..3 are HA Slots 1..4 (Slot 1 = T0, Slot 4 = T3)."""
    printer = printer_of(api.snapshot, 101)
    template = printer["loaded"][0]
    spools = api.snapshot["spools"]
    printer["loaded"] = [
        {
            **template,
            "slot": s,
            "slotLabel": f"T{s}",
            "spoolId": spools[s]["id"],
            "spoolNumber": spools[s]["spoolNumber"],
            "name": f"Tool {s}",
            "remainingGrams": 10.0 + s,
        }
        for s in range(4)
    ]
    printer["currentPrint"]["filament"] = [
        {"slot": 3, "spoolId": spools[3]["id"], "estimatedGrams": 50.0}
    ]
    await setup_entry(hass)

    def sensor(suffix: str):
        return hass.states.get(entity_id(hass, "sensor", suffix))

    assert sensor("printer:101:slot:1:filament").state == "Tool 0"
    assert sensor("printer:101:slot:1:filament").attributes["slot_label"] == "T0"
    assert sensor("printer:101:slot:4:filament").state == "Tool 3"
    assert sensor("printer:101:slot:4:filament").attributes["slot_label"] == "T3"
    assert sensor("printer:101:slot:4:remaining").state == "13.0"
    assert bstate(hass, "printer:101:slot:4:filament_short").state == "on"
    assert bstate(hass, "printer:101:slot:1:filament_short").state == "unknown"
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", "printlog", "api.3dprintlog.com:42:printer:101:slot:5:filament"
        )
        is None
    )


@pytest.mark.parametrize(
    ("open_maintenance", "expected"),
    [
        ({"count": 1, "nextDate": "2026-10-17T14:59:00+00:00"}, "on"),
        ({"count": 1, "nextDate": "2026-10-17T15:00:00+00:00"}, "on"),
        ({"count": 1, "nextDate": "2026-10-17T15:01:00+00:00"}, "off"),
        ({"count": 0, "nextDate": None}, "off"),
        (None, "off"),
    ],
)
async def test_maintenance_due(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    open_maintenance,
    expected,
) -> None:
    printer_of(api.snapshot, 101)["openMaintenance"] = open_maintenance
    await setup_entry(hass)
    state = bstate(hass, "printer:101:maintenance_due")
    assert state.state == expected
    if open_maintenance is None:
        assert state.attributes["open_count"] == 0
        assert state.attributes["next_date"] is None


async def test_maintenance_due_evaluated_on_update(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    freezer,
) -> None:
    entry = await setup_entry(hass)
    assert bstate(hass, "printer:101:maintenance_due").state == "off"
    freezer.move_to(NOW + timedelta(days=30))

    def bump(snapshot: dict) -> None:
        printer_of(snapshot, 101)["openMaintenance"]["count"] = 3

    api.set_change("v2", '"s2"', bump)
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    assert bstate(hass, "printer:101:maintenance_due").state == "on"


async def test_maintenance_due_flips_with_time_and_no_data_change(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    freezer,
) -> None:
    printer_of(api.snapshot, 101)["openMaintenance"] = {
        "count": 1,
        "nextDate": "2026-10-17T16:00:00+00:00",
    }
    entry = await setup_entry(hass)
    assert bstate(hass, "printer:101:maintenance_due").state == "off"

    freezer.move_to(NOW + timedelta(hours=1, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert bstate(hass, "printer:101:maintenance_due").state == "on"

    # The snapshot was unchanged (a 304), so only the timer could flip the state.
    # Unloading cancels the pending timer cleanly.
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
