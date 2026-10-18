"""Sensors of the 3D Print Log integration (spec 5.1, 5.3)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, tzinfo
from functools import partial
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import MAX_LENGTH_STATE_STATE, UnitOfMass, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import PrintLogConfigEntry
from .coordinator import SlowCoordinator, SnapshotCoordinator
from .entity import (
    EntitySpec,
    PrintLogAccountEntity,
    PrintLogPrinterEntity,
    PrintLogSlotEntity,
    async_add_printer_entities,
    slot_numbers,
)
from .models import Account, Printer

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

LAST_PRINT_RESULTS = ["success", "failed", "canceled", "partial_success"]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _truncate(value: str) -> str:
    """Home Assistant rejects a state longer than 255 characters."""
    return value[:MAX_LENGTH_STATE_STATE]


@dataclass(frozen=True, kw_only=True)
class PrinterSensorDescription(SensorEntityDescription):
    """A sensor of a printer device."""

    value_fn: Callable[[Printer], StateType | datetime]
    attrs_fn: Callable[[Printer], dict[str, Any]] | None = None
    exists_fn: Callable[[Printer], bool] = lambda printer: True


@dataclass(frozen=True, kw_only=True)
class SlotSensorDescription(SensorEntityDescription):
    """A sensor of one filament slot."""

    value_fn: Callable[[Printer, int], StateType]
    attrs_fn: Callable[[Printer, int], dict[str, Any]] | None = None
    exists_fn: Callable[[Printer], bool] = lambda printer: True


@dataclass(frozen=True, kw_only=True)
class AccountSensorDescription(SensorEntityDescription):
    """A sensor of the account device."""

    value_fn: Callable[[Account], StateType]
    attrs_fn: Callable[[Account], dict[str, Any]] | None = None


def _current_print_state(printer: Printer) -> str | None:
    current = printer.current_print
    if current is None:
        return None
    return _truncate(current.title or "Untitled")


def _current_print_attrs(printer: Printer) -> dict[str, Any]:
    current = printer.current_print
    if current is None:
        return {}
    connection = printer.connection
    return {
        "print_id": current.id,
        "source": current.source,
        "url": current.url,
        "connection_stale": connection is not None and connection.status == "stale",
    }


def _last_result_state(printer: Printer) -> str | None:
    finished = printer.last_finished_print
    if finished is None or finished.status not in LAST_PRINT_RESULTS:
        return None
    return finished.status


def _last_result_attrs(printer: Printer) -> dict[str, Any]:
    finished = printer.last_finished_print
    if finished is None:
        return {}
    return {
        "print_id": finished.id,
        "title": finished.title,
        "finished_at": _iso(finished.finished_at),
        "source": finished.source,
        "url": finished.url,
    }


def _print_time_hours(printer: Printer) -> float | None:
    if printer.print_time_seconds is None:
        return None
    return round(printer.print_time_seconds / 3600, 1)


def _last_maintenance_state(printer: Printer) -> datetime | None:
    return printer.last_maintenance.date if printer.last_maintenance else None


def _last_maintenance_attrs(printer: Printer) -> dict[str, Any]:
    maintenance = printer.last_maintenance
    if maintenance is None:
        return {}
    return {"category": maintenance.category, "description": maintenance.description}


def _slot_filament_state(printer: Printer, slot: int) -> str | None:
    spool = printer.loaded_in_slot(slot)
    if spool is None:
        return None
    return _truncate(spool.name or spool.material or f"Spool #{spool.spool_number}")


def _slot_filament_attrs(printer: Printer, slot: int) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    spool = printer.loaded_in_slot(slot)
    if spool is not None:
        attrs = {
            "material": spool.material,
            "color_hex": spool.color_hex,
            "colors": list(spool.colors),
            "spool_id": spool.spool_id,
            "spool_number": spool.spool_number,
            "slot_label": spool.slot_label,
        }
    if slot == 1 and (extra := printer.unslotted()):
        attrs["unslotted"] = [
            {
                "spool_id": s.spool_id,
                "spool_number": s.spool_number,
                "name": s.name,
                "material": s.material,
                "remaining_grams": s.remaining_grams,
            }
            for s in extra
        ]
    return attrs


def _slot_remaining_state(printer: Printer, slot: int) -> float | None:
    spool = printer.loaded_in_slot(slot)
    return spool.remaining_grams if spool else None


def _slot_remaining_attrs(printer: Printer, slot: int) -> dict[str, Any]:
    spool = printer.loaded_in_slot(slot)
    return {"spool_id": spool.spool_id} if spool else {}


PRINTER_SENSORS: tuple[PrinterSensorDescription, ...] = (
    PrinterSensorDescription(
        key="current_print",
        translation_key="current_print",
        value_fn=_current_print_state,
        attrs_fn=_current_print_attrs,
    ),
    PrinterSensorDescription(
        key="print_started",
        translation_key="print_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: p.current_print.started_at if p.current_print else None,
    ),
    PrinterSensorDescription(
        key="print_estimated_end",
        translation_key="print_estimated_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: p.current_print.estimated_end_at if p.current_print else None,
    ),
    PrinterSensorDescription(
        key="last_print_result",
        translation_key="last_print_result",
        device_class=SensorDeviceClass.ENUM,
        options=LAST_PRINT_RESULTS,
        value_fn=_last_result_state,
        attrs_fn=_last_result_attrs,
    ),
    PrinterSensorDescription(
        key="print_time",
        translation_key="print_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=_print_time_hours,
        exists_fn=lambda p: p.has_extended_fields,
    ),
    PrinterSensorDescription(
        key="last_maintenance",
        translation_key="last_maintenance",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_maintenance_state,
        attrs_fn=_last_maintenance_attrs,
        exists_fn=lambda p: p.has_extended_fields,
    ),
)

SLOT_SENSORS: tuple[SlotSensorDescription, ...] = (
    SlotSensorDescription(
        key="filament",
        translation_key="slot_filament",
        value_fn=_slot_filament_state,
        attrs_fn=_slot_filament_attrs,
    ),
    SlotSensorDescription(
        key="remaining",
        translation_key="slot_remaining",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_slot_remaining_state,
        attrs_fn=_slot_remaining_attrs,
    ),
)

ACCOUNT_SENSORS: tuple[AccountSensorDescription, ...] = (
    AccountSensorDescription(
        key="prints_this_month",
        translation_key="prints_this_month",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda a: a.print_count,
        attrs_fn=lambda a: {
            "month": a.month,
            "time_zone": a.time_zone,
            "success_count": a.success_count,
            "failed_count": a.failed_count,
        },
    ),
    AccountSensorDescription(
        key="grams_this_month",
        translation_key="grams_this_month",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda a: a.grams,
        attrs_fn=lambda a: {"month": a.month},
    ),
)


def month_start(account: Account, zone: tzinfo) -> datetime | None:
    """The start of `account.month` in `zone` (the account's resolved time zone)."""
    try:
        year, month = account.month.split("-")
        return datetime(int(year), int(month), 1, tzinfo=zone)
    except ValueError:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""

    def build(coordinator: SnapshotCoordinator, printer: Printer) -> Iterator[EntitySpec]:
        for description in PRINTER_SENSORS:
            if description.exists_fn(printer):
                yield (
                    description.key,
                    None,
                    partial(PrinterSensor, coordinator, printer.id, description),
                )
        for slot in slot_numbers(printer):
            for slot_description in SLOT_SENSORS:
                if slot_description.exists_fn(printer):
                    yield (
                        slot_description.key,
                        slot,
                        partial(SlotSensor, coordinator, printer.id, slot_description, slot),
                    )

    async_add_printer_entities(entry, async_add_entities, build)

    data = entry.runtime_data
    entities: list[SensorEntity] = [
        AccountMonthSensor(data.snapshot, entry, description) for description in ACCOUNT_SENSORS
    ]
    entities.append(UnreadNotificationsSensor(data.slow, entry))
    async_add_entities(entities)


class PrinterSensor(PrintLogPrinterEntity, SensorEntity):
    """A printer sensor."""

    entity_description: PrinterSensorDescription

    @property
    def native_value(self) -> StateType | datetime:
        printer = self.printer
        return self.entity_description.value_fn(printer) if printer else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        printer = self.printer
        fn = self.entity_description.attrs_fn
        return fn(printer) if printer and fn else None


class SlotSensor(PrintLogSlotEntity, SensorEntity):
    """A filament slot sensor."""

    entity_description: SlotSensorDescription

    @property
    def native_value(self) -> StateType:
        printer = self.printer
        return self.entity_description.value_fn(printer, self.slot) if printer else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        printer = self.printer
        fn = self.entity_description.attrs_fn
        return fn(printer, self.slot) if printer and fn else None


class AccountMonthSensor(PrintLogAccountEntity, SensorEntity):
    """A this-month total of the account."""

    entity_description: AccountSensorDescription

    @property
    def native_value(self) -> StateType:
        data = self.coordinator.data
        return self.entity_description.value_fn(data.account) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        fn = self.entity_description.attrs_fn
        return fn(data.account) if data and fn else None

    @property
    def last_reset(self) -> datetime | None:
        data = self.coordinator.data
        # The zone is resolved by the coordinator; reading it does no I/O.
        return month_start(data.account, self.coordinator.account_zone) if data else None


class UnreadNotificationsSensor(PrintLogAccountEntity, SensorEntity):
    """Unread notifications, from the slow coordinator."""

    def __init__(self, coordinator: SlowCoordinator, entry: PrintLogConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            SensorEntityDescription(
                key="unread_notifications",
                translation_key="unread_notifications",
                state_class=SensorStateClass.MEASUREMENT,
            ),
        )

    @property
    def native_value(self) -> StateType:
        data = self.coordinator.data
        return data.get("unreadCount") if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        return {"unread_achievements": data.get("unreadAchievementCount")} if data else None
