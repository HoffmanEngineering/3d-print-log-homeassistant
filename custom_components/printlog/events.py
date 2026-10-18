"""Derive print started/finished/failed events from successive snapshots.

Pure Python: no Home Assistant imports. Implements spec 6.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .const import EVENT_WINDOW_MINUTES, SEEN_LIMIT
from .models import Snapshot

BUS_EVENT = {
    "started": "printlog_print_started",
    "success": "printlog_print_finished",
    "failed": "printlog_print_failed",
    "canceled": "printlog_print_failed",
    "partial_success": "printlog_print_failed",
}


@dataclass(frozen=True, slots=True)
class PrinterEventState:
    """Persisted per-printer state. `seen` is ordered most recently added last."""

    seen: tuple[int, ...]
    current: int | None

    def to_dict(self) -> dict:
        return {"seen": list(self.seen), "current": self.current}

    @classmethod
    def from_dict(cls, d: dict) -> PrinterEventState:
        return cls(seen=tuple(d.get("seen") or ()), current=d.get("current"))


@dataclass(frozen=True, slots=True)
class PrintEvent:
    """One event to fire on the bus."""

    printer_id: int
    printer_name: str
    status: str
    print_id: int
    title: str | None
    started_at: datetime | None
    finished_at: datetime | None
    source: str | None
    url: str | None


def _add_seen(seen: tuple[int, ...], print_id: int) -> tuple[int, ...]:
    if print_id in seen:
        return seen
    return (*seen, print_id)[-SEEN_LIMIT:]


def diff(
    prev: dict[int, PrinterEventState], snapshot: Snapshot, now: datetime
) -> tuple[list[PrintEvent], dict[int, PrinterEventState]]:
    """Return the events for this snapshot and the new state map."""
    window = timedelta(minutes=EVENT_WINDOW_MINUTES)
    events: list[PrintEvent] = []
    state: dict[int, PrinterEventState] = {}

    for printer in snapshot.printers:
        cur = printer.current_print
        fin = printer.last_finished_print
        cur_id = cur.id if cur else None
        before = prev.get(printer.id)

        if before is None:
            seen = _add_seen((), fin.id) if fin else ()
            state[printer.id] = PrinterEventState(seen=seen, current=cur_id)
            continue

        seen = before.seen
        started: PrintEvent | None = None
        finished: PrintEvent | None = None

        if cur is not None and cur.id != before.current and cur.id not in seen:
            started = PrintEvent(
                printer_id=printer.id,
                printer_name=printer.display_name,
                status="started",
                print_id=cur.id,
                title=cur.title,
                started_at=cur.started_at,
                finished_at=None,
                source=cur.source,
                url=cur.url,
            )

        if fin is not None and fin.id not in seen:
            reference = fin.finished_at or fin.started_at
            recent = reference is not None and now - reference <= window
            if fin.id == before.current or recent:
                finished = PrintEvent(
                    printer_id=printer.id,
                    printer_name=printer.display_name,
                    status=fin.status,
                    print_id=fin.id,
                    title=fin.title,
                    started_at=fin.started_at,
                    finished_at=fin.finished_at,
                    source=fin.source,
                    url=fin.url,
                )
            seen = _add_seen(seen, fin.id)

        # A finish comes before the next start when both happen between polls.
        if finished:
            events.append(finished)
        if started:
            events.append(started)
        state[printer.id] = PrinterEventState(seen=seen, current=cur_id)

    return events, state
