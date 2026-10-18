"""Tests for the pure event diff (spec 6.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.printlog.events import (
    BUS_EVENT,
    PrinterEventState,
    PrintEvent,
    diff,
)
from custom_components.printlog.models import (
    Account,
    CurrentPrint,
    FinishedPrint,
    Printer,
    Snapshot,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _cur(pid: int, started: datetime | None = None) -> CurrentPrint:
    return CurrentPrint(
        id=pid,
        title=f"cur {pid}",
        started_at=started or NOW - timedelta(minutes=5),
        estimated_seconds=None,
        estimated_end_at=None,
        source="Moonraker",
        url=f"https://x/{pid}",
        filament=(),
    )


def _fin(
    pid: int,
    status: str = "success",
    finished_ago: timedelta | None = timedelta(minutes=2),
    started_ago: timedelta = timedelta(hours=1),
) -> FinishedPrint:
    return FinishedPrint(
        id=pid,
        title=f"fin {pid}",
        status=status,
        started_at=NOW - started_ago,
        finished_at=None if finished_ago is None else NOW - finished_ago,
        source="Moonraker",
        url=f"https://x/{pid}",
    )


def _printer(pid=1, current=None, finished=None, name="Voron") -> Printer:
    return Printer(
        id=pid,
        name=name,
        make=None,
        model=None,
        active=True,
        slot_count=1,
        connection=None,
        current_print=current,
        last_finished_print=finished,
        loaded=(),
        print_time_seconds=None,
        last_maintenance=None,
        open_maintenance=None,
        has_extended_fields=True,
    )


def _snap(*printers: Printer) -> Snapshot:
    return Snapshot(
        schema_version=1,
        version="1.18",
        printers=tuple(printers),
        spools=(),
        account=Account(
            month="2026-10",
            time_zone="UTC",
            print_count=0,
            grams=0.0,
            success_count=None,
            failed_count=None,
        ),
    )


def _state(seen=(), current=None) -> PrinterEventState:
    return PrinterEventState(seen=tuple(seen), current=current)


def test_first_sight_seeds_no_events():
    snap = _snap(_printer(current=_cur(5), finished=_fin(4)))
    events, state = diff({}, snap, NOW)
    assert events == []
    assert state[1] == PrinterEventState(seen=(4,), current=5)


def test_first_sight_is_per_printer():
    prev = {1: _state(seen=(4,), current=None)}
    snap = _snap(
        _printer(1, finished=_fin(4)),
        _printer(2, current=_cur(9), finished=_fin(8)),
    )
    events, state = diff(prev, snap, NOW)
    assert events == []
    assert state[2] == PrinterEventState(seen=(8,), current=9)


def test_started_fires_once():
    prev = {1: _state(seen=(4,), current=None)}
    snap = _snap(_printer(current=_cur(5), finished=_fin(4)))
    events, state = diff(prev, snap, NOW)
    assert [(e.status, e.print_id) for e in events] == [("started", 5)]
    e = events[0]
    assert e.printer_id == 1
    assert e.printer_name == "Voron"
    assert e.title == "cur 5"
    assert e.finished_at is None
    assert e.source == "Moonraker"
    assert e.url == "https://x/5"
    events2, state2 = diff(state, snap, NOW)
    assert events2 == []
    assert state2 == state


def test_finished_fires_once():
    prev = {1: _state(seen=(4,), current=None)}
    snap = _snap(_printer(finished=_fin(5)))
    events, state = diff(prev, snap, NOW)
    assert [(e.status, e.print_id) for e in events] == [("success", 5)]
    events2, _ = diff(state, snap, NOW)
    assert events2 == []


@pytest.mark.parametrize("status", ["success", "failed", "canceled", "partial_success"])
def test_finish_of_previous_current_fires_status(status):
    prev = {1: _state(seen=(4,), current=5)}
    # Finished long ago by reference time, but it was the current print.
    snap = _snap(_printer(finished=_fin(5, status, finished_ago=timedelta(hours=5))))
    events, state = diff(prev, snap, NOW)
    assert [(e.status, e.print_id) for e in events] == [(status, 5)]
    assert events[0].finished_at == NOW - timedelta(hours=5)
    assert state[1].current is None
    assert 5 in state[1].seen
    assert status in BUS_EVENT


def test_bus_event_names():
    assert BUS_EVENT == {
        "started": "printlog_print_started",
        "success": "printlog_print_finished",
        "failed": "printlog_print_failed",
        "canceled": "printlog_print_failed",
        "partial_success": "printlog_print_failed",
    }


def test_restart_window():
    prev = {1: _state(seen=(4,), current=None)}
    fire = _snap(_printer(finished=_fin(5, finished_ago=timedelta(minutes=30))))
    events, _ = diff(prev, fire, NOW)
    assert [e.print_id for e in events] == [5]

    old = _snap(_printer(finished=_fin(6, finished_ago=timedelta(minutes=90))))
    events, state = diff(prev, old, NOW)
    assert events == []
    assert 6 in state[1].seen


def test_finished_without_finished_at_uses_started_at():
    prev = {1: _state(seen=(4,))}
    recent = _snap(_printer(finished=_fin(5, finished_ago=None, started_ago=timedelta(minutes=20))))
    events, _ = diff(prev, recent, NOW)
    assert [e.print_id for e in events] == [5]
    assert events[0].finished_at is None

    old = _snap(_printer(finished=_fin(6, finished_ago=None, started_ago=timedelta(hours=3))))
    events, _ = diff(prev, old, NOW)
    assert events == []


def test_deleted_newest_resurfaces_older_no_event():
    prev = {1: _state(seen=(9, 10))}
    snap = _snap(_printer(finished=_fin(9, finished_ago=timedelta(minutes=5))))
    events, _ = diff(prev, snap, NOW)
    assert events == []

    prev = {1: _state(seen=(10,))}
    snap = _snap(_printer(finished=_fin(9, finished_ago=timedelta(hours=5))))
    events, state = diff(prev, snap, NOW)
    assert events == []
    assert 9 in state[1].seen


def test_backdated_manual_print_no_event():
    prev = {1: _state(seen=(4,))}
    manual = _fin(7, finished_ago=None, started_ago=timedelta(days=3))
    events, state = diff(prev, _snap(_printer(finished=manual)), NOW)
    assert events == []
    assert 7 in state[1].seen


def test_current_deleted_no_event():
    prev = {1: _state(seen=(4,), current=5)}
    snap = _snap(_printer(finished=_fin(4)))
    events, state = diff(prev, snap, NOW)
    assert events == []
    assert state[1].current is None


def test_seen_bounded_to_50():
    prev = {1: _state(seen=tuple(range(1, 51)), current=None)}
    snap = _snap(_printer(finished=_fin(100)))
    events, state = diff(prev, snap, NOW)
    assert len(events) == 1
    seen = state[1].seen
    assert len(seen) == 50
    assert seen[-1] == 100
    assert 1 not in seen
    assert seen[0] == 2


def test_seen_no_duplicates():
    prev = {1: _state(seen=(4, 5))}
    snap = _snap(_printer(finished=_fin(4)))
    _, state = diff(prev, snap, NOW)
    assert state[1].seen == (4, 5)


def test_printer_removed_drops_state():
    prev = {1: _state(seen=(4,)), 2: _state(seen=(8,))}
    snap = _snap(_printer(1, finished=_fin(4)))
    _, state = diff(prev, snap, NOW)
    assert set(state) == {1}


def test_state_roundtrip():
    s = PrinterEventState(seen=(1, 2, 3), current=3)
    assert s.to_dict() == {"seen": [1, 2, 3], "current": 3}
    assert PrinterEventState.from_dict(s.to_dict()) == s
    assert PrinterEventState.from_dict({"seen": [], "current": None}) == PrinterEventState(
        seen=(), current=None
    )


def test_short_print_only_finish():
    prev = {1: _state(seen=(4,), current=None)}
    snap = _snap(_printer(finished=_fin(5, finished_ago=timedelta(minutes=1))))
    events, _ = diff(prev, snap, NOW)
    assert [(e.status, e.print_id) for e in events] == [("success", 5)]


def test_finish_then_start_ordering():
    prev = {1: _state(seen=(4,), current=5)}
    snap = _snap(_printer(current=_cur(6), finished=_fin(5)))
    events, state = diff(prev, snap, NOW)
    assert [(e.status, e.print_id) for e in events] == [("success", 5), ("started", 6)]
    assert state[1].current == 6
    events2, _ = diff(state, snap, NOW)
    assert events2 == []


def test_print_event_is_dataclass_with_fields():
    e = PrintEvent(1, "P", "started", 2, None, None, None, None, None)
    assert e.print_id == 2
