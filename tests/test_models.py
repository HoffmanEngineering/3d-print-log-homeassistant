"""Tests for the snapshot models."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from custom_components.printlog.models import Snapshot

FIXTURES = Path(__file__).parent / "fixtures"
S1 = "11111111-1111-4111-8111-111111111111"
S3 = "33333333-3333-4333-8333-333333333333"
S4 = "44444444-4444-4444-8444-444444444444"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_full():
    snap = Snapshot.from_json(load("snapshot_full.json"))
    assert snap.schema_version == 1
    assert snap.version == "v-abc123"
    assert len(snap.printers) == 2
    assert len(snap.spools) == 5

    p = snap.printers[0]
    assert (p.id, p.name, p.make, p.model) == (101, "Snapmaker U1", "Snapmaker", "U1")
    assert p.active is True
    assert p.slot_count == 4
    assert p.has_extended_fields is True
    assert p.print_time_seconds == 432000
    assert (p.connection.kind, p.connection.status) == ("moonraker", "online")
    assert p.last_finished_print is None
    assert p.last_maintenance.id == "66666666-6666-4666-8666-666666666666"
    assert p.last_maintenance.date == datetime(2026, 9, 30, tzinfo=UTC)
    assert p.last_maintenance.category == "Lubrication"
    assert p.last_maintenance.description == "Oiled the rails"
    assert p.open_maintenance.count == 2
    assert p.open_maintenance.next_date == datetime(2026, 11, 1, tzinfo=UTC)

    cp = p.current_print
    assert (cp.id, cp.title) == (9001, "Benchy")
    assert cp.started_at == datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
    assert cp.started_at.utcoffset() == timedelta(0)
    assert cp.estimated_seconds == 7200
    assert cp.estimated_end_at == datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
    assert cp.source == "moonraker"
    assert cp.url == "https://3dprintlog.com/prints/9001"
    assert [(f.slot, f.spool_id, f.estimated_grams) for f in cp.filament] == [
        (1, S1, 18.5),
        (3, S3, 4.25),
    ]

    assert len(p.loaded) == 3
    ls = p.loaded[0]
    assert (ls.slot, ls.slot_label, ls.spool_id, ls.spool_number) == (1, "T0", S1, 1)
    assert (ls.name, ls.material, ls.color_hex) == ("Galaxy Black", "PLA", "#1A1A1A")
    assert ls.colors == ("#1A1A1A",)
    assert ls.remaining_grams == 742.5
    assert p.loaded[1].colors == ("#FF0000", "#00FF00", "#0000FF")
    assert p.loaded[2].slot is None

    p2 = snap.printers[1]
    assert p2.name is None and p2.make is None and p2.model is None
    assert p2.connection is None
    assert p2.current_print is None
    f = p2.last_finished_print
    assert (f.id, f.title, f.status) == (8999, "Phone stand", "success")
    assert f.started_at == datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
    assert f.finished_at == datetime(2026, 10, 16, 10, 30, tzinfo=UTC)
    assert (f.source, f.url) == ("manual", "https://3dprintlog.com/prints/8999")
    assert p2.last_maintenance is None and p2.open_maintenance is None

    sp = snap.spools[0]
    assert (sp.id, sp.spool_number, sp.name, sp.brand) == (S1, 1, "Galaxy Black", "Prusament")
    assert (sp.material, sp.category, sp.color_name) == ("PLA", "filament", "Black")
    assert sp.color_hex == "#1A1A1A" and sp.colors == ("#1A1A1A",)
    assert (sp.remaining_grams, sp.used_grams, sp.initial_grams) == (742.5, 257.5, 1000.0)
    assert sp.storage_location == "Shelf A"
    assert sp.active is True
    untracked = snap.spools[4]
    assert untracked.remaining_grams is None and untracked.initial_grams is None
    assert untracked.used_grams == 0.0
    assert untracked.colors == () and untracked.active is False

    a = snap.account
    assert (a.month, a.time_zone, a.print_count, a.grams) == (
        "2026-10",
        "America/Chicago",
        12,
        384.5,
    )
    assert (a.success_count, a.failed_count) == (10, 2)

    assert snap.spool_by_id(S3).spool_number == 3
    assert snap.spool_by_id("nope") is None
    assert snap.spool_by_number(4).id == S4
    assert snap.spool_by_number(99) is None


def test_parse_v1_17_missing_fields_are_none():
    snap = Snapshot.from_json(load("snapshot_v1_17.json"))
    p = snap.printers[0]
    assert p.has_extended_fields is False
    assert p.print_time_seconds is None
    assert p.last_maintenance is None
    assert p.open_maintenance is None
    assert p.current_print.source is None
    assert p.current_print.url is None
    assert p.current_print.filament == ()
    f = snap.printers[1].last_finished_print
    assert f.source is None and f.url is None
    assert snap.account.success_count is None
    assert snap.account.failed_count is None


def test_unknown_fields_ignored():
    data = load("snapshot_full.json")
    data["futureThing"] = 1
    data["printers"][0]["extra"] = {"a": 1}
    data["printers"][0]["currentPrint"]["surprise"] = True
    data["spools"][0]["mystery"] = "x"
    data["account"]["other"] = 2
    assert Snapshot.from_json(data) == Snapshot.from_json(load("snapshot_full.json"))


def test_display_name_fallback():
    snap = Snapshot.from_json(load("snapshot_full.json"))
    assert snap.printers[0].display_name == "Snapmaker U1"
    assert snap.printers[1].display_name == "Printer 102"


def test_loaded_in_slot_unslotted_single_slot():
    p = Snapshot.from_json(load("snapshot_full.json")).printers[1]
    assert p.slot_count == 1
    assert p.loaded_in_slot(1).spool_number == 2
    assert p.loaded_in_slot(2) is None
    assert p.unslotted() == ()


def test_loaded_in_slot_unslotted_single_slot_occupied():
    data = load("snapshot_full.json")
    p = data["printers"][1]
    slotted = copy.deepcopy(p["loaded"][0])
    slotted["slot"] = 0  # API slot 0 (T0) = HA slot 1
    slotted["spoolNumber"] = 3
    p["loaded"].insert(0, slotted)
    printer = Snapshot.from_json(data).printers[1]
    assert printer.loaded_in_slot(1).spool_number == 3
    assert len(printer.unslotted()) == 1


def test_loaded_in_slot_unslotted_multi_slot():
    p = Snapshot.from_json(load("snapshot_full.json")).printers[0]
    assert p.slot_count == 4
    assert p.loaded_in_slot(1).spool_id == S1
    assert p.loaded_in_slot(3).spool_id == S3
    assert p.loaded_in_slot(2) is None
    assert [s.spool_id for s in p.unslotted()] == [S4]


def test_loaded_in_slot_multi_slot_without_slot_one():
    data = load("snapshot_full.json")
    data["printers"][0]["loaded"] = [data["printers"][0]["loaded"][2]]
    p = Snapshot.from_json(data).printers[0]
    assert p.loaded_in_slot(1) is None
    assert len(p.unslotted()) == 1


def _spool(slot, number, label=None):
    return {
        "slot": slot,
        "slotLabel": label,
        "spoolId": f"{number:08d}-0000-4000-8000-000000000000",
        "spoolNumber": number,
        "name": f"Spool {number}",
        "remainingGrams": 100.0 * number,
    }


def _printer(slot_count, loaded, filament=None):
    current = None
    if filament is not None:
        current = {"id": 1, "filament": filament}
    return Snapshot.from_json(
        {
            "schemaVersion": 1,
            "version": "v",
            "printers": [
                {"id": 1, "slotCount": slot_count, "loaded": loaded, "currentPrint": current}
            ],
            "account": {"month": "2026-10", "timeZone": "UTC", "printCount": 0, "grams": 0},
        }
    ).printers[0]


def test_api_slots_are_zero_based():
    """Ruling R11: API slot s is HA slot s + 1."""
    p = _printer(1, [_spool(0, 1, "T0")], [{"slot": 0, "estimatedGrams": 5.0}])
    assert p.loaded[0].slot == 1
    assert p.current_print.filament[0].slot == 1
    assert p.loaded_in_slot(1).spool_number == 1
    assert p.estimated_grams_in_slot(1) == 5.0


def test_single_slot_spool_at_api_slot_zero_is_slot_one():
    p = _printer(1, [_spool(0, 1, "T0")])
    assert p.loaded_in_slot(1).spool_number == 1
    assert p.loaded_in_slot(2) is None
    assert p.unslotted() == ()


def test_multi_slot_api_zero_to_three():
    loaded = [_spool(s, s + 1, f"T{s}") for s in range(4)]
    p = _printer(4, loaded)
    assert p.loaded_in_slot(1).slot_label == "T0"
    assert p.loaded_in_slot(4).slot_label == "T3"
    assert p.loaded_in_slot(5) is None
    assert p.loaded_in_slot(0) is None


def test_null_slot_on_single_slot_printer_is_slot_one():
    p = _printer(1, [_spool(None, 1)], [{"slot": None, "estimatedGrams": 7.0}])
    assert p.loaded_in_slot(1).spool_number == 1
    assert p.estimated_grams_in_slot(1) == 7.0
    assert p.estimated_grams_in_slot(2) is None


def test_null_slot_not_promoted_over_api_slot_zero():
    p = _printer(
        1,
        [_spool(0, 1), _spool(None, 2)],
        [{"slot": None, "estimatedGrams": 7.0}, {"slot": 0, "estimatedGrams": 3.0}],
    )
    assert p.loaded_in_slot(1).spool_number == 1
    assert [s.spool_number for s in p.unslotted()] == [2]
    # The slotted estimate wins over the unslotted one, as the spool does.
    assert p.estimated_grams_in_slot(1) == 3.0


def test_null_slot_estimate_on_multi_slot_printer_is_not_slot_one():
    p = _printer(4, [_spool(0, 1)], [{"slot": None, "estimatedGrams": 7.0}])
    assert p.estimated_grams_in_slot(1) is None


def test_two_unslotted_spools():
    single = _printer(1, [_spool(None, 1), _spool(None, 2)])
    assert single.loaded_in_slot(1).spool_number == 1
    assert [s.spool_number for s in single.unslotted()] == [2]
    multi = _printer(4, [_spool(None, 1), _spool(None, 2)])
    assert multi.loaded_in_slot(1) is None
    assert [s.spool_number for s in multi.unslotted()] == [1, 2]


def test_estimate_at_api_slot_zero_matches_ha_slot_one():
    p = Snapshot.from_json(load("snapshot_full.json")).printers[0]
    assert p.estimated_grams_in_slot(1) == 18.5
    assert p.estimated_grams_in_slot(2) is None
    assert p.estimated_grams_in_slot(3) == 4.25
    assert p.loaded_in_slot(3).slot_label == "T2"


def test_no_current_print_no_estimate():
    p = _printer(1, [_spool(0, 1)])
    assert p.estimated_grams_in_slot(1) is None


def test_equality():
    assert Snapshot.from_json(load("snapshot_full.json")) == Snapshot.from_json(
        load("snapshot_full.json")
    )
    changed = load("snapshot_full.json")
    changed["account"]["printCount"] = 13
    assert Snapshot.from_json(changed) != Snapshot.from_json(load("snapshot_full.json"))


def test_z_suffix_parses_aware():
    data = load("snapshot_full.json")
    data["printers"][0]["currentPrint"]["startedAt"] = "2026-10-17T14:00:00Z"
    cp = Snapshot.from_json(data).printers[0].current_print
    assert cp.started_at.tzinfo is not None


def test_naive_timestamp_treated_as_utc():
    data = load("snapshot_full.json")
    data["printers"][0]["currentPrint"]["startedAt"] = "2026-10-17T14:00:00"
    cp = Snapshot.from_json(data).printers[0].current_print
    assert cp.started_at == datetime(2026, 10, 17, 14, 0, tzinfo=UTC)


def test_seven_digit_fraction_parses():
    data = load("snapshot_full.json")
    data["printers"][0]["currentPrint"]["startedAt"] = "2026-10-17T14:00:00.1234567+00:00"
    cp = Snapshot.from_json(data).printers[0].current_print
    assert cp.started_at.year == 2026
    assert cp.started_at.microsecond == 123456
    assert cp.started_at.tzinfo is not None


def test_empty_object_is_not_none():
    data = load("snapshot_full.json")
    data["printers"][0]["connection"] = {"kind": "moonraker", "status": "online"}
    data["printers"][0]["openMaintenance"] = {"count": 0}
    om = Snapshot.from_json(data).printers[0].open_maintenance
    assert om is not None and om.count == 0 and om.next_date is None
