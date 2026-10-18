"""Coordinators and runtime data for the 3D Print Log integration."""

from __future__ import annotations

from dataclasses import dataclass

from .api import PrintLogClient


@dataclass
class PrintLogData:
    """Runtime data stored on the config entry (extended in a later task)."""

    client: PrintLogClient
