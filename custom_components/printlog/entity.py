"""Base entities, device info and the dynamic entity adder (spec 4.6, 5.1)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .api import normalized_host
from .const import CONF_BASE_URL, DOMAIN, PROD_API_HOST, PROD_WEB_BASE
from .coordinator import SnapshotCoordinator
from .models import Printer


def printer_unique_id(entry_uid: str, printer_id: int, key: str, slot: int | None = None) -> str:
    """Unique id of a printer or slot entity (spec 4.6)."""
    if slot is None:
        return f"{entry_uid}:printer:{printer_id}:{key}"
    return f"{entry_uid}:printer:{printer_id}:slot:{slot}:{key}"


def _is_production(entry: ConfigEntry) -> bool:
    try:
        return normalized_host(entry.data[CONF_BASE_URL]) == PROD_API_HOST
    except KeyError, ValueError:
        return False


def printer_device_info(entry: ConfigEntry, printer: Printer, entry_uid: str) -> DeviceInfo:
    """Device info for a printer. Never contains the API key."""
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry_uid}:printer:{printer.id}")},
        name=printer.display_name,
    )
    if printer.make:
        info["manufacturer"] = printer.make
    if printer.model:
        info["model"] = printer.model
    if _is_production(entry):
        info["configuration_url"] = f"{PROD_WEB_BASE}/printers/{printer.id}"
    return info


def account_device_info(entry: ConfigEntry, entry_uid: str) -> DeviceInfo:
    """Device info for the account service device."""
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry_uid}:account")},
        name="3D Print Log",
        entry_type=DeviceEntryType.SERVICE,
    )
    if _is_production(entry):
        info["configuration_url"] = PROD_WEB_BASE
    return info


class PrintLogPrinterEntity(CoordinatorEntity[SnapshotCoordinator]):
    """An entity of a printer device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SnapshotCoordinator,
        printer_id: int,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.printer_id = printer_id
        self.entity_description = description
        self._attr_unique_id = printer_unique_id(
            coordinator.entry_uid, printer_id, description.key, getattr(self, "slot", None)
        )
        printer = self.printer
        if printer is not None:
            self._attr_device_info = printer_device_info(
                coordinator.config_entry, printer, coordinator.entry_uid
            )

    @property
    def printer(self) -> Printer | None:
        """The printer in the latest snapshot, or None when it is absent."""
        data = self.coordinator.data
        if data is None:
            return None
        return next((p for p in data.printers if p.id == self.printer_id), None)

    @property
    def available(self) -> bool:
        return super().available and self.printer is not None


class PrintLogSlotEntity(PrintLogPrinterEntity):
    """An entity of one filament slot. Keyed on the slot number, never the spool."""

    def __init__(
        self,
        coordinator: SnapshotCoordinator,
        printer_id: int,
        description: EntityDescription,
        slot: int,
    ) -> None:
        self.slot = slot
        super().__init__(coordinator, printer_id, description)
        self._attr_translation_placeholders = {"slot": str(slot)}


class PrintLogAccountEntity(CoordinatorEntity[Any]):
    """An entity of the account device. The coordinator is chosen by the caller."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[Any],
        entry: ConfigEntry,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        uid = entry.runtime_data.snapshot.entry_uid
        self.entity_description = description
        self._attr_unique_id = f"{uid}:account:{description.key}"
        self._attr_device_info = account_device_info(entry, uid)


def slot_numbers(printer: Printer) -> range:
    """Slot numbers 1..max(slotCount, 1)."""
    return range(1, max(printer.slot_count, 1) + 1)


type EntityFactory = Callable[[], PrintLogPrinterEntity]
type EntitySpec = tuple[str, int | None, EntityFactory]


@callback
def async_add_printer_entities(
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[SnapshotCoordinator, Printer], Iterable[EntitySpec]],
) -> None:
    """Add entities for the current snapshot, and for anything new on each update.

    `build` yields (key, slot, factory) for every entity a printer should have.
    New printers, a grown slot count and newly appearing extended fields yield
    unique ids that are not known yet; only those are constructed. A removed
    entity (for example after its device was deleted) leaves `known`, so a
    printer that comes back gets its entities again.
    """
    coordinator: SnapshotCoordinator = entry.runtime_data.snapshot
    known: set[str] = set()
    registry = dr.async_get(coordinator.hass)

    def _forget(uid: str) -> None:
        known.discard(uid)

    @callback
    def _sync_device(printer: Printer) -> None:
        info = printer_device_info(entry, printer, coordinator.entry_uid)
        ident = next(iter(info["identifiers"]))
        device = registry.async_get_device_by_identifier(ident, entry.entry_id)
        if device is None:
            return
        name, make, model = info["name"], info.get("manufacturer"), info.get("model")
        if (device.name, device.manufacturer, device.model) != (name, make, model):
            registry.async_update_device(device.id, name=name, manufacturer=make, model=model)

    @callback
    def _sync() -> None:
        if coordinator.data is None:
            return
        new: list[Entity] = []
        for printer in coordinator.data.printers:
            _sync_device(printer)
            for key, slot, factory in build(coordinator, printer):
                uid = printer_unique_id(coordinator.entry_uid, printer.id, key, slot)
                if uid in known:
                    continue
                known.add(uid)
                entity = factory()
                entity.async_on_remove(partial(_forget, uid))
                new.append(entity)
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
