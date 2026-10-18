"""Diagnostics tests."""

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.printlog.const import DOMAIN
from custom_components.printlog.diagnostics import async_get_config_entry_diagnostics

BASE = "https://api.3dprintlog.com"
SNAPSHOT = Path(__file__).parent / "fixtures" / "snapshot_full.json"
API_KEY = "plk_super_secret_key_123"


async def test_diagnostics_redacts(hass: HomeAssistant, aioclient_mock, snapshot, freezer) -> None:
    freezer.move_to("2026-10-17T20:00:00+00:00")
    aioclient_mock.get(f"{BASE}/api/sync/version", json={"version": "v1"})
    aioclient_mock.get(
        f"{BASE}/api/sync/snapshot", json=json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    )
    aioclient_mock.get(f"{BASE}/api/Notifications/unread-count", json={"count": 0})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Chris",
        unique_id="api.3dprintlog.com:42",
        version=1,
        minor_version=1,
        data={"base_url": BASE, "auth_type": "api_key", "api_key": API_KEY},
        options={"scan_interval": 45},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await async_get_config_entry_diagnostics(hass, entry)

    dumped = json.dumps(result, default=str)
    for secret in (API_KEY, "Benchy", "Phone stand", "Snapmaker U1", "Galaxy Black", "Chris"):
        assert secret not in dumped
    assert result["entry"]["data"]["api_key"] == "**REDACTED**"
    assert result["entry"]["options"] == {"scan_interval": 45}
    assert result == snapshot
