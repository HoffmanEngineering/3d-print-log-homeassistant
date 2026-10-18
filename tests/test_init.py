"""Entry setup, unload and migration tests."""

import json
from pathlib import Path

from awesomeversion import AwesomeVersion
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.printlog import async_migrate_entry
from custom_components.printlog.const import DOMAIN

MANIFEST = Path(__file__).parent.parent / "custom_components" / "printlog" / "manifest.json"
BASE = "https://api.3dprintlog.com"
VERSION_URL = f"{BASE}/api/sync/version"
SNAPSHOT_URL = f"{BASE}/api/sync/snapshot"
UNREAD_URL = f"{BASE}/api/Notifications/unread-count"
SNAPSHOT = Path(__file__).parent / "fixtures" / "snapshot_full.json"


def make_entry(**kwargs) -> MockConfigEntry:
    defaults = {
        "domain": DOMAIN,
        "title": "Chris",
        "unique_id": "api.3dprintlog.com:42",
        "version": 1,
        "minor_version": 1,
        "data": {"base_url": BASE, "auth_type": "api_key", "api_key": "k"},
    }
    defaults.update(kwargs)
    return MockConfigEntry(**defaults)


def test_manifest_matches_const():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert AwesomeVersion(manifest["version"]).valid


async def test_setup_and_unload(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(VERSION_URL, json={"version": "v1"})
    aioclient_mock.get(SNAPSHOT_URL, json=json.loads(SNAPSHOT.read_text(encoding="utf-8")))
    aioclient_mock.get(UNREAD_URL, json={"count": 0})
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is not None
    assert entry.runtime_data.snapshot.data is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_auth_failure_starts_reauth(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(VERSION_URL, status=401)
    entry = make_entry()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_setup_connection_failure_retries(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(VERSION_URL, status=503)
    entry = make_entry()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_migrate_current_version_ok(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is True


async def test_migrate_future_version_fails(hass: HomeAssistant) -> None:
    entry = make_entry(version=2)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is False
