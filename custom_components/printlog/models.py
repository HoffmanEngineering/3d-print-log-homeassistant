"""Typed models for the 3D Print Log sync snapshot (GET /api/sync/snapshot).

Pure Python: no Home Assistant imports. Unknown JSON fields are ignored, and
fields added after API v1.17 parse to None (or an empty tuple) when absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _strs(value: Any) -> tuple[str, ...]:
    return tuple(value or ())


@dataclass(frozen=True, slots=True)
class Connection:
    """A printer connector's health."""

    kind: str
    status: str

    @classmethod
    def from_json(cls, d: dict) -> Connection:
        return cls(kind=d["kind"], status=d["status"])


@dataclass(frozen=True, slots=True)
class PrintFilament:
    """One filament row of the current print."""

    slot: int | None
    spool_id: str | None
    estimated_grams: float | None

    @classmethod
    def from_json(cls, d: dict) -> PrintFilament:
        return cls(
            slot=d.get("slot"),
            spool_id=d.get("spoolId"),
            estimated_grams=d.get("estimatedGrams"),
        )


@dataclass(frozen=True, slots=True)
class CurrentPrint:
    """The newest print in the Printing state."""

    id: int
    title: str | None
    started_at: datetime | None
    estimated_seconds: int | None
    estimated_end_at: datetime | None
    source: str | None
    url: str | None
    filament: tuple[PrintFilament, ...]

    @classmethod
    def from_json(cls, d: dict) -> CurrentPrint:
        return cls(
            id=d["id"],
            title=d.get("title"),
            started_at=_dt(d.get("startedAt")),
            estimated_seconds=d.get("estimatedSeconds"),
            estimated_end_at=_dt(d.get("estimatedEndAt")),
            source=d.get("source"),
            url=d.get("url"),
            filament=tuple(PrintFilament.from_json(f) for f in d.get("filament") or ()),
        )


@dataclass(frozen=True, slots=True)
class FinishedPrint:
    """The newest finished print."""

    id: int
    title: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    source: str | None
    url: str | None

    @classmethod
    def from_json(cls, d: dict) -> FinishedPrint:
        return cls(
            id=d["id"],
            title=d.get("title"),
            status=d["status"],
            started_at=_dt(d.get("startedAt")),
            finished_at=_dt(d.get("finishedAt")),
            source=d.get("source"),
            url=d.get("url"),
        )


@dataclass(frozen=True, slots=True)
class LoadedSpool:
    """A spool currently loaded on a printer."""

    slot: int | None
    slot_label: str | None
    spool_id: str
    spool_number: int | None
    name: str | None
    material: str | None
    color_hex: str | None
    colors: tuple[str, ...]
    remaining_grams: float | None

    @classmethod
    def from_json(cls, d: dict) -> LoadedSpool:
        return cls(
            slot=d.get("slot"),
            slot_label=d.get("slotLabel"),
            spool_id=d["spoolId"],
            spool_number=d.get("spoolNumber"),
            name=d.get("name"),
            material=d.get("material"),
            color_hex=d.get("colorHex"),
            colors=_strs(d.get("colors")),
            remaining_grams=d.get("remainingGrams"),
        )


@dataclass(frozen=True, slots=True)
class Maintenance:
    """A printer's most recent maintenance entry."""

    id: str
    date: datetime | None
    category: str | None
    description: str | None

    @classmethod
    def from_json(cls, d: dict) -> Maintenance:
        return cls(
            id=d["id"],
            date=_dt(d.get("date")),
            category=d.get("category"),
            description=d.get("description"),
        )


@dataclass(frozen=True, slots=True)
class OpenMaintenance:
    """Open maintenance tasks for a printer."""

    count: int
    next_date: datetime | None

    @classmethod
    def from_json(cls, d: dict) -> OpenMaintenance:
        return cls(count=d["count"], next_date=_dt(d.get("nextDate")))


@dataclass(frozen=True, slots=True)
class Printer:
    """One printer: one Home Assistant device."""

    id: int
    name: str | None
    make: str | None
    model: str | None
    active: bool
    slot_count: int
    connection: Connection | None
    current_print: CurrentPrint | None
    last_finished_print: FinishedPrint | None
    loaded: tuple[LoadedSpool, ...]
    print_time_seconds: int | None
    last_maintenance: Maintenance | None
    open_maintenance: OpenMaintenance | None
    has_extended_fields: bool

    @classmethod
    def from_json(cls, d: dict) -> Printer:
        def opt(key: str, model: Any) -> Any:
            value = d.get(key)
            return model.from_json(value) if value is not None else None

        return cls(
            id=d["id"],
            name=d.get("name"),
            make=d.get("make"),
            model=d.get("model"),
            active=d.get("active", True),
            slot_count=d.get("slotCount", 1),
            connection=opt("connection", Connection),
            current_print=opt("currentPrint", CurrentPrint),
            last_finished_print=opt("lastFinishedPrint", FinishedPrint),
            loaded=tuple(LoadedSpool.from_json(s) for s in d.get("loaded") or ()),
            print_time_seconds=d.get("printTimeSeconds"),
            last_maintenance=opt("lastMaintenance", Maintenance),
            open_maintenance=opt("openMaintenance", OpenMaintenance),
            has_extended_fields="printTimeSeconds" in d,
        )

    @property
    def display_name(self) -> str:
        return self.name or f"Printer {self.id}"

    def _promoted(self) -> LoadedSpool | None:
        # Spec 5.1: on a single-slot printer, an unslotted spool counts as
        # slot 1 when slot 1 is otherwise empty.
        if self.slot_count > 1 or any(s.slot == 1 for s in self.loaded):
            return None
        return next((s for s in self.loaded if s.slot is None), None)

    def loaded_in_slot(self, n: int) -> LoadedSpool | None:
        for spool in self.loaded:
            if spool.slot == n:
                return spool
        if n == 1:
            return self._promoted()
        return None

    def unslotted(self) -> tuple[LoadedSpool, ...]:
        promoted = self._promoted()
        return tuple(s for s in self.loaded if s.slot is None and s is not promoted)


@dataclass(frozen=True, slots=True)
class Spool:
    """A material the user owns."""

    id: str
    spool_number: int | None
    name: str | None
    brand: str | None
    material: str | None
    category: str
    color_name: str | None
    color_hex: str | None
    colors: tuple[str, ...]
    remaining_grams: float | None
    used_grams: float
    initial_grams: float | None
    storage_location: str | None
    active: bool

    @classmethod
    def from_json(cls, d: dict) -> Spool:
        return cls(
            id=d["id"],
            spool_number=d.get("spoolNumber"),
            name=d.get("name"),
            brand=d.get("brand"),
            material=d.get("material"),
            category=d["category"],
            color_name=d.get("colorName"),
            color_hex=d.get("colorHex"),
            colors=_strs(d.get("colors")),
            remaining_grams=d.get("remainingGrams"),
            used_grams=d.get("usedGrams", 0.0),
            initial_grams=d.get("initialGrams"),
            storage_location=d.get("storageLocation"),
            active=d.get("active", True),
        )


@dataclass(frozen=True, slots=True)
class Account:
    """Account totals for the current month."""

    month: str
    time_zone: str
    print_count: int
    grams: float
    success_count: int | None
    failed_count: int | None

    @classmethod
    def from_json(cls, d: dict) -> Account:
        return cls(
            month=d["month"],
            time_zone=d["timeZone"],
            print_count=d["printCount"],
            grams=d["grams"],
            success_count=d.get("successCount"),
            failed_count=d.get("failedCount"),
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything the integration renders, in one response."""

    schema_version: int
    version: str
    printers: tuple[Printer, ...]
    spools: tuple[Spool, ...]
    account: Account

    @classmethod
    def from_json(cls, data: dict) -> Snapshot:
        return cls(
            schema_version=data["schemaVersion"],
            version=data["version"],
            printers=tuple(Printer.from_json(p) for p in data.get("printers") or ()),
            spools=tuple(Spool.from_json(s) for s in data.get("spools") or ()),
            account=Account.from_json(data["account"]),
        )

    def spool_by_id(self, guid: str) -> Spool | None:
        return next((s for s in self.spools if s.id == guid), None)

    def spool_by_number(self, n: int) -> Spool | None:
        return next((s for s in self.spools if s.spool_number == n), None)
