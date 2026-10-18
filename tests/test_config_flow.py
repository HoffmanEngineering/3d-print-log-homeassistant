"""Config flow tests."""

from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.printlog.const import DOMAIN

BASE = "https://api.3dprintlog.com"
KEY = "plk_secret_key_value"
ME_URL = f"{BASE}/api/Users/me"


@pytest.fixture(autouse=True)
def no_setup():
    with patch("custom_components.printlog.async_setup_entry", return_value=True) as m:
        yield m


def make_entry(**kwargs) -> MockConfigEntry:
    defaults = {
        "domain": DOMAIN,
        "title": "Chris",
        "unique_id": "api.3dprintlog.com:42",
        "version": 1,
        "minor_version": 1,
        "data": {"base_url": BASE, "auth_type": "api_key", "api_key": "old-key"},
    }
    defaults.update(kwargs)
    return MockConfigEntry(**defaults)


async def start_user(hass: HomeAssistant, user_input: dict):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_user_success(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(ME_URL, json={"id": 42, "displayName": "Chris"})
    result = await start_user(hass, {"base_url": BASE + "/", "api_key": KEY})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Chris"
    assert result["data"] == {"base_url": BASE, "auth_type": "api_key", "api_key": KEY}
    entry = result["result"]
    assert entry.unique_id == "api.3dprintlog.com:42"
    assert KEY not in entry.title
    assert KEY not in entry.unique_id


async def test_user_title_fallback(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(ME_URL, json={"id": 42, "displayName": None})
    result = await start_user(hass, {"base_url": BASE, "api_key": KEY})
    assert result["title"] == "3D Print Log"


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, "invalid_auth"), (403, "invalid_auth"), (500, "cannot_connect")],
)
async def test_user_http_errors(hass: HomeAssistant, aioclient_mock, status, error) -> None:
    aioclient_mock.get(ME_URL, status=status)
    result = await start_user(hass, {"base_url": BASE, "api_key": KEY})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_user_connection_error(hass: HomeAssistant, aioclient_mock) -> None:
    import aiohttp

    aioclient_mock.get(ME_URL, exc=aiohttp.ClientError)
    result = await start_user(hass, {"base_url": BASE, "api_key": KEY})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_unknown_error(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.printlog.config_flow.PrintLogClient.get_me",
        side_effect=RuntimeError("boom"),
    ):
        result = await start_user(hass, {"base_url": BASE, "api_key": KEY})
    assert result["errors"] == {"base": "unknown"}


async def test_user_invalid_url(hass: HomeAssistant) -> None:
    result = await start_user(hass, {"base_url": "http://host:abc", "api_key": KEY})
    assert result["errors"] == {"base": "invalid_url"}


async def test_user_recovers_after_error(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(ME_URL, status=401)
    result = await start_user(hass, {"base_url": BASE, "api_key": KEY})
    assert result["errors"] == {"base": "invalid_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(ME_URL, json={"id": 42, "displayName": "Chris"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": BASE, "api_key": KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_already_configured(hass: HomeAssistant, aioclient_mock) -> None:
    make_entry().add_to_hass(hass)
    aioclient_mock.get(ME_URL, json={"id": 42, "displayName": "Chris"})
    result = await start_user(hass, {"base_url": BASE, "api_key": KEY})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_success(hass: HomeAssistant, aioclient_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get(ME_URL, json={"id": 42, "displayName": "Chris"})
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_key": "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == "new"
    assert entry.data["base_url"] == BASE


async def test_reauth_wrong_account(hass: HomeAssistant, aioclient_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get(ME_URL, json={"id": 99, "displayName": "Other"})
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_key": "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["api_key"] == "old-key"


async def test_reauth_invalid_auth(hass: HomeAssistant, aioclient_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get(ME_URL, status=401)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_key": "new"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_success(hass: HomeAssistant, aioclient_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get(ME_URL, json={"id": 42, "displayName": "Chris"})
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": BASE + "/", "api_key": "newer"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"base_url": BASE, "auth_type": "api_key", "api_key": "newer"}


async def test_reconfigure_wrong_account(hass: HomeAssistant, aioclient_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get(ME_URL, json={"id": 7, "displayName": "Other"})
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": BASE, "api_key": "k"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["api_key"] == "old-key"


async def test_reconfigure_different_host_is_wrong_account(
    hass: HomeAssistant, aioclient_mock
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get("http://localhost:5000/api/Users/me", json={"id": 42, "displayName": "C"})
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": "http://localhost:5000", "api_key": "k"}
    )
    assert result["reason"] == "wrong_account"


async def test_reconfigure_error(hass: HomeAssistant, aioclient_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    aioclient_mock.get(ME_URL, status=500)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": BASE, "api_key": "k"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_default_and_save(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema = result["data_schema"].schema
    key = next(k for k in schema if k == "scan_interval")
    assert key.default() == 30
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"scan_interval": 60}


@pytest.mark.parametrize("value", [14, 301])
async def test_options_out_of_range_rejected(hass: HomeAssistant, value) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": value}
        )


async def test_options_uses_saved_value_as_default(hass: HomeAssistant) -> None:
    entry = make_entry(options={"scan_interval": 90})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    key = next(k for k in result["data_schema"].schema if k == "scan_interval")
    assert key.default() == 90


async def test_api_key_never_logged(hass: HomeAssistant, aioclient_mock, caplog) -> None:
    caplog.set_level("DEBUG")
    aioclient_mock.get(ME_URL, status=401)
    result = await start_user(hass, {"base_url": BASE, "api_key": KEY})
    assert result["errors"] == {"base": "invalid_auth"}
    with patch(
        "custom_components.printlog.config_flow.PrintLogClient.get_me",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"base_url": BASE, "api_key": KEY}
        )
    assert result["errors"] == {"base": "unknown"}
    assert caplog.records
    assert KEY not in caplog.text
