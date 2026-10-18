"""Sensor, device info and dynamic entity tests."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from custom_components.printlog.const import DOMAIN
from custom_components.printlog.entity import printer_device_info

from .test_coordinator import (
    FIXTURES,
    NOW,
    UID,
    FakeApi,
    api,  # noqa: F401  (fixture)
    make_entry,
    setup_entry,
)


@pytest.fixture(autouse=True)
def _frozen_now(freezer):
    freezer.move_to(NOW)


def printer_of(snapshot: dict, printer_id: int) -> dict:
    return next(p for p in snapshot["printers"] if p["id"] == printer_id)


def entity_id(hass: HomeAssistant, platform: str, suffix: str) -> str:
    """Entity id from a unique id suffix (after the entry uid)."""
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{UID}:{suffix}")
    assert found is not None, suffix
    return found


def platform_snapshot(
    hass: HomeAssistant, registry: er.EntityRegistry, entry: MockConfigEntry, platform: str
) -> dict:
    """Deterministic registry and state data of every entity of one platform.

    HA's snapshot_platform serializes random ids (device, entity, entry), so the
    comparison would never be stable for a custom integration.
    """
    result: dict = {}
    for reg in sorted(
        er.async_entries_for_config_entry(registry, entry.entry_id), key=lambda e: e.entity_id
    ):
        if reg.domain != platform:
            continue
        state = hass.states.get(reg.entity_id)
        result[reg.entity_id] = {
            "unique_id": reg.unique_id,
            "translation_key": reg.translation_key,
            "original_name": reg.original_name,
            "original_device_class": str(reg.original_device_class),
            "unit": reg.unit_of_measurement,
            "device": next(iter(dr.async_get(hass).async_get(reg.device_id).identifiers))[1],
            "state": state.state,
            "attributes": dict(state.attributes),
        }
    return result


async def test_entities_full_snapshot(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    entry = await setup_entry(hass)
    assert platform_snapshot(hass, entity_registry, entry, "sensor") == snapshot


async def test_printer_101_states(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)

    def state(suffix: str, platform: str = "sensor"):
        return hass.states.get(entity_id(hass, platform, suffix))

    current = state("printer:101:current_print")
    assert current.state == "Benchy"
    assert current.attributes["print_id"] == 9001
    assert current.attributes["source"] == "moonraker"
    assert current.attributes["url"] == "https://3dprintlog.com/prints/9001"
    assert current.attributes["connection_stale"] is False
    assert state("printer:101:print_started").state == "2026-10-17T14:00:00+00:00"
    assert state("printer:101:print_estimated_end").state == "2026-10-17T16:00:00+00:00"
    assert state("printer:101:last_print_result").state == "unknown"
    assert state("printer:101:print_time").state == "120.0"
    last = state("printer:101:last_maintenance")
    assert last.state == "2026-09-30T00:00:00+00:00"
    assert last.attributes["category"] == "Lubrication"
    assert last.attributes["description"] == "Oiled the rails"

    one = state("printer:101:slot:1:filament")
    assert one.state == "Galaxy Black"
    assert one.attributes["material"] == "PLA"
    assert one.attributes["color_hex"] == "#1A1A1A"
    assert one.attributes["colors"] == ["#1A1A1A"]
    assert one.attributes["spool_number"] == 1
    assert one.attributes["slot_label"] == "T0"
    assert one.attributes["spool_id"] == "11111111-1111-4111-8111-111111111111"
    assert [s["spool_number"] for s in one.attributes["unslotted"]] == [4]
    assert state("printer:101:slot:2:filament").state == "unknown"
    assert state("printer:101:slot:3:filament").attributes["colors"] == [
        "#FF0000",
        "#00FF00",
        "#0000FF",
    ]
    assert state("printer:101:slot:1:remaining").state == "742.5"
    assert state("printer:101:slot:1:remaining").attributes["spool_id"].startswith("1111")
    assert state("printer:101:slot:2:remaining").state == "unknown"
    assert hass.states.get(entity_id(hass, "sensor", "printer:101:slot:4:remaining"))

    # Printer 102: single slot, unslotted spool promoted to slot 1.
    assert state("printer:102:current_print").state == "unknown"
    result = state("printer:102:last_print_result")
    assert result.state == "success"
    assert result.attributes["print_id"] == 8999
    assert result.attributes["title"] == "Phone stand"
    assert result.attributes["source"] == "manual"
    assert result.attributes["finished_at"] == "2026-10-16T10:30:00+00:00"
    assert state("printer:102:slot:1:filament").state == "Signal Red"
    assert "unslotted" not in state("printer:102:slot:1:filament").attributes
    assert state("printer:102:slot:1:remaining").state == "310.0"
    assert state("printer:102:last_maintenance").state == "unknown"


async def test_account_states(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    prints = hass.states.get(entity_id(hass, "sensor", "account:prints_this_month"))
    assert prints.state == "12"
    assert prints.attributes["month"] == "2026-10"
    assert prints.attributes["time_zone"] == "America/Chicago"
    assert prints.attributes["success_count"] == 10
    assert prints.attributes["failed_count"] == 2
    grams = hass.states.get(entity_id(hass, "sensor", "account:grams_this_month"))
    assert grams.state == "384.5"
    assert grams.attributes["month"] == "2026-10"
    unread = hass.states.get(entity_id(hass, "sensor", "account:unread_notifications"))
    assert unread.state == "3"
    assert unread.attributes["unread_achievements"] == 1


async def test_month_totals_last_reset(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    expected = datetime(2026, 10, 1, tzinfo=ZoneInfo("America/Chicago"))
    for key in ("prints_this_month", "grams_this_month"):
        state = hass.states.get(entity_id(hass, "sensor", f"account:{key}"))
        assert datetime.fromisoformat(state.attributes["last_reset"]) == expected
        assert state.attributes["state_class"] == "total"


async def test_month_totals_unknown_zone_falls_back(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    api.snapshot["account"]["timeZone"] = "Not/AZone"
    await setup_entry(hass)
    state = hass.states.get(entity_id(hass, "sensor", "account:prints_this_month"))
    assert datetime.fromisoformat(state.attributes["last_reset"]) == datetime(
        2026, 10, 1, tzinfo=ZoneInfo("Europe/Berlin")
    )


async def test_entities_v1_17_snapshot_omits_extended(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    entity_registry: er.EntityRegistry,
) -> None:
    api.snapshot = json.loads((FIXTURES / "snapshot_v1_17.json").read_text(encoding="utf-8"))
    await setup_entry(hass)
    unique_ids = {e.unique_id for e in entity_registry.entities.values()}
    assert unique_ids
    for forbidden in ("print_time", "last_maintenance", "maintenance_due", "filament_short"):
        assert not [u for u in unique_ids if u.endswith(f":{forbidden}")], forbidden
    # The base entities exist.
    assert any(u.endswith(":current_print") for u in unique_ids)
    assert any(u.endswith(":slot:1:filament") for u in unique_ids)


async def test_extended_entities_added_when_fields_appear(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    entity_registry: er.EntityRegistry,
) -> None:
    old = json.loads((FIXTURES / "snapshot_v1_17.json").read_text(encoding="utf-8"))
    api.snapshot = old
    entry = await setup_entry(hass)
    count = len(entity_registry.entities)
    assert not [e for e in entity_registry.entities.values() if e.unique_id.endswith(":print_time")]

    full = json.loads((FIXTURES / "snapshot_full.json").read_text(encoding="utf-8"))
    api.set_change("v2", '"s2"')
    api.snapshot = {**full, "version": "v2"}
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()

    ids = {e.unique_id for e in entity_registry.entities.values()}
    assert f"{UID}:printer:101:print_time" in ids
    assert f"{UID}:printer:101:last_maintenance" in ids
    assert len(entity_registry.entities) > count


async def test_slot_entities_added_when_slot_count_grows(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    entity_registry: er.EntityRegistry,
) -> None:
    entry = await setup_entry(hass)
    uid = f"{UID}:printer:102:slot:2:filament"
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, uid) is None

    def grow(snapshot: dict) -> None:
        printer_of(snapshot, 102)["slotCount"] = 2

    api.set_change("v2", '"s2"', grow)
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    for key in ("filament", "remaining"):
        assert entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{UID}:printer:102:slot:2:{key}"
        )
    assert entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{UID}:printer:102:slot:2:filament_short"
    )
    # Slot 1 keeps its entity (no duplicates).
    assert [
        e
        for e in entity_registry.entities.values()
        if e.unique_id == f"{UID}:printer:102:slot:1:filament"
    ].__len__() == 1


async def test_new_printer_gets_entities_and_missing_printer_unavailable(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    entity_registry: er.EntityRegistry,
) -> None:
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.snapshot

    def drop(snapshot: dict) -> None:
        snapshot["printers"] = [p for p in snapshot["printers"] if p["id"] != 102]

    api.set_change("v2", '"s2"', drop)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, "sensor", "printer:102:current_print")).state == (
        "unavailable"
    )
    assert hass.states.get(entity_id(hass, "sensor", "printer:101:current_print")).state == "Benchy"

    def add(snapshot: dict) -> None:
        snapshot["printers"].append(
            {"id": 103, "name": "New", "slotCount": 1, "loaded": [], "currentPrint": None}
        )

    api.set_change("v3", '"s3"', add)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, f"{UID}:printer:103:current_print")


async def test_untracked_remaining_unknown(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    printer_of(api.snapshot, 101)["loaded"][0]["remainingGrams"] = None
    await setup_entry(hass)
    state = hass.states.get(entity_id(hass, "sensor", "printer:101:slot:1:remaining"))
    assert state.state == "unknown"
    # The slot's filament sensor still reports the spool.
    assert hass.states.get(entity_id(hass, "sensor", "printer:101:slot:1:filament")).state == (
        "Galaxy Black"
    )


async def test_slot_filament_fallback_names(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    loaded = printer_of(api.snapshot, 101)["loaded"]
    loaded[0]["name"] = None
    loaded[1]["name"] = None
    loaded[1]["material"] = None
    await setup_entry(hass)
    assert hass.states.get(entity_id(hass, "sensor", "printer:101:slot:1:filament")).state == "PLA"
    assert (
        hass.states.get(entity_id(hass, "sensor", "printer:101:slot:3:filament")).state
        == "Spool #3"
    )


async def test_current_print_connection_stale_attribute(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    printer_of(api.snapshot, 101)["connection"]["status"] = "stale"
    await setup_entry(hass)
    state = hass.states.get(entity_id(hass, "sensor", "printer:101:current_print"))
    assert state.attributes["connection_stale"] is True


async def test_current_print_untitled(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    printer_of(api.snapshot, 101)["currentPrint"]["title"] = None
    await setup_entry(hass)
    assert hass.states.get(entity_id(hass, "sensor", "printer:101:current_print")).state == (
        "Untitled"
    )


async def test_unread_notifications_from_slow(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    entry = await setup_entry(hass)
    unread = entity_id(hass, "sensor", "account:unread_notifications")
    prints = entity_id(hass, "sensor", "account:prints_this_month")
    current = entity_id(hass, "sensor", "printer:101:current_print")

    # The slow coordinator fails: only the unread sensor goes unavailable.
    api.unread_status = 500
    await entry.runtime_data.slow.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(unread).state == "unavailable"
    assert hass.states.get(prints).state == "12"

    # The snapshot coordinator fails: unread recovers, the rest goes unavailable.
    api.unread_status = 200
    api.unread_body = {"unreadCount": 7, "unreadAchievementCount": 2}
    await entry.runtime_data.slow.async_refresh()
    api.version_status = 500
    await entry.runtime_data.snapshot.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(unread).state == "7"
    assert hass.states.get(unread).attributes["unread_achievements"] == 2
    assert hass.states.get(current).state == "unavailable"
    assert hass.states.get(prints).state == "unavailable"


async def test_device_info(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    entry = await setup_entry(hass)
    registry = dr.async_get(hass)

    def device(ident: str):
        return registry.async_get_device_by_identifier((DOMAIN, f"{UID}:{ident}"), entry.entry_id)

    named = device("printer:101")
    assert named.name == "Snapmaker U1"
    assert named.manufacturer == "Snapmaker"
    assert named.model == "U1"
    assert named.configuration_url == "https://www.3dprintlog.com/printers/101"
    assert named.via_device_id is None

    unnamed = device("printer:102")
    assert unnamed.name == "Printer 102"
    assert unnamed.configuration_url == "https://www.3dprintlog.com/printers/102"

    account = device("account")
    assert account.name == "3D Print Log"
    assert account.entry_type is dr.DeviceEntryType.SERVICE
    assert account.configuration_url == "https://www.3dprintlog.com"


async def test_device_info_configuration_url_only_for_production(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    entry = await setup_entry(hass)
    printer = entry.runtime_data.snapshot.data.printers[0]
    for url, expected in (
        ("https://api.3dprintlog.com", True),
        ("https://API.3dprintlog.com:443/", True),
        ("https://printlog.example.com", False),
        ("http://192.168.1.5:5000", False),
        ("not a url", False),
    ):
        fake = MockConfigEntry(domain=DOMAIN, unique_id=UID, data={"base_url": url})
        info = printer_device_info(fake, printer)
        assert ("configuration_url" in info) is expected, url
        assert info["identifiers"] == {(DOMAIN, f"{UID}:printer:101")}
    # The API key is never part of device info.
    assert "secret-key" not in str(printer_device_info(make_entry(), printer))
