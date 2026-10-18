"""Per-printer print event entity (spec 5.2)."""

from __future__ import annotations

import logging
from functools import partial

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PrintLogConfigEntry
from .coordinator import SnapshotCoordinator
from .entity import EntitySpec, PrintLogPrinterEntity, async_add_printer_entities
from .events import EVENT_TYPES, PrintEvent, events_payload
from .models import Printer

_LOGGER = logging.getLogger(__package__)

PARALLEL_UPDATES = 0

PRINT_EVENT = EventEntityDescription(
    key="print",
    translation_key="print",
    event_types=list(EVENT_TYPES),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one event entity per printer."""

    def build(coordinator: SnapshotCoordinator, printer: Printer) -> list[EntitySpec]:
        return [("print", None, partial(PrintEventEntity, coordinator, printer.id, PRINT_EVENT))]

    async_add_printer_entities(entry, async_add_entities, build)


class PrintEventEntity(PrintLogPrinterEntity, EventEntity):
    """Fires when a print of this printer starts or finishes."""

    entity_description: EventEntityDescription

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Registered during platform setup, which is before the coordinator
        # starts dispatching, so events of the first refresh reach it too.
        self.async_on_remove(self.coordinator.async_add_event_listener(self._handle_events))

    @callback
    def _handle_events(self, events: list[PrintEvent]) -> None:
        for event in events:
            if event.printer_id != self.printer_id:
                continue
            try:
                data = events_payload(event, self.coordinator.config_entry.entry_id, None)
                data = {k: v for k, v in data.items() if k != "device_id"}
                self._trigger_event(event.status, data)
                # Once per event, so a finish and a start in one pass both reach
                # the state machine.
                self.async_write_ha_state()
            except Exception:
                _LOGGER.exception("Could not record print event %s", event)
