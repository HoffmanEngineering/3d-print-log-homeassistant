"""Binary sensors of the 3D Print Log integration (spec 5.1)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import PrintLogConfigEntry
from .coordinator import SnapshotCoordinator
from .entity import (
    PrintLogPrinterEntity,
    PrintLogSlotEntity,
    async_add_printer_entities,
    slot_numbers,
)
from .models import Printer

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PrinterBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor of a printer device."""

    value_fn: Callable[[Printer], bool | None]
    attrs_fn: Callable[[Printer], dict[str, Any]] | None = None
    exists_fn: Callable[[Printer], bool] = lambda printer: True


@dataclass(frozen=True, kw_only=True)
class SlotBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor of one filament slot."""

    value_fn: Callable[[Printer, int], bool | None]
    attrs_fn: Callable[[Printer, int], dict[str, Any]] | None = None
    exists_fn: Callable[[Printer], bool] = lambda printer: True


def _maintenance_due(printer: Printer) -> bool:
    open_maintenance = printer.open_maintenance
    if open_maintenance is None or open_maintenance.count <= 0:
        return False
    next_date = open_maintenance.next_date
    return next_date is not None and next_date <= dt_util.utcnow()


def _maintenance_attrs(printer: Printer) -> dict[str, Any]:
    open_maintenance = printer.open_maintenance
    if open_maintenance is None:
        return {"open_count": 0, "next_date": None}
    next_date = open_maintenance.next_date
    return {
        "open_count": open_maintenance.count,
        "next_date": next_date.isoformat() if next_date else None,
    }


def _connection_state(printer: Printer) -> bool | None:
    if printer.connection is None:
        return None
    return printer.connection.status == "online"


def _slot_numbers_for_print(printer: Printer, slot: int) -> tuple[float | None, float | None]:
    """Return (estimated_grams, remaining_grams) for a slot of the current print."""
    current = printer.current_print
    if current is None:
        return None, None
    estimate = next(
        (
            row.estimated_grams
            for row in current.filament
            if row.slot == slot or (row.slot is None and slot == 1 and printer.slot_count <= 1)
        ),
        None,
    )
    spool = printer.loaded_in_slot(slot)
    return estimate, spool.remaining_grams if spool else None


def _filament_short(printer: Printer, slot: int) -> bool | None:
    estimate, remaining = _slot_numbers_for_print(printer, slot)
    if estimate is None or remaining is None:
        return None
    return remaining < estimate


def _filament_short_attrs(printer: Printer, slot: int) -> dict[str, Any]:
    estimate, remaining = _slot_numbers_for_print(printer, slot)
    return {"estimated_grams": estimate, "remaining_grams": remaining}


PRINTER_BINARY_SENSORS: tuple[PrinterBinarySensorDescription, ...] = (
    PrinterBinarySensorDescription(
        key="connection",
        translation_key="connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=_connection_state,
        attrs_fn=lambda p: {"kind": p.connection.kind} if p.connection else {},
    ),
    PrinterBinarySensorDescription(
        key="printing",
        translation_key="printing",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda p: p.current_print is not None,
    ),
    PrinterBinarySensorDescription(
        key="maintenance_due",
        translation_key="maintenance_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_maintenance_due,
        attrs_fn=_maintenance_attrs,
        exists_fn=lambda p: p.has_extended_fields,
    ),
)

SLOT_BINARY_SENSORS: tuple[SlotBinarySensorDescription, ...] = (
    SlotBinarySensorDescription(
        key="filament_short",
        translation_key="slot_filament_short",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_filament_short,
        attrs_fn=_filament_short_attrs,
        exists_fn=lambda p: p.has_extended_fields,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""

    def build(coordinator: SnapshotCoordinator, printer: Printer):
        for description in PRINTER_BINARY_SENSORS:
            if description.exists_fn(printer):
                yield PrinterBinarySensor(coordinator, printer.id, description)
        for slot in slot_numbers(printer):
            for slot_description in SLOT_BINARY_SENSORS:
                if slot_description.exists_fn(printer):
                    yield SlotBinarySensor(coordinator, printer.id, slot_description, slot)

    async_add_printer_entities(entry, async_add_entities, build)


class PrinterBinarySensor(PrintLogPrinterEntity, BinarySensorEntity):
    """A printer binary sensor."""

    entity_description: PrinterBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        printer = self.printer
        return self.entity_description.value_fn(printer) if printer else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        printer = self.printer
        fn = self.entity_description.attrs_fn
        return fn(printer) if printer and fn else None


class SlotBinarySensor(PrintLogSlotEntity, BinarySensorEntity):
    """A filament slot binary sensor."""

    entity_description: SlotBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        printer = self.printer
        return self.entity_description.value_fn(printer, self.slot) if printer else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        printer = self.printer
        fn = self.entity_description.attrs_fn
        return fn(printer, self.slot) if printer and fn else None
