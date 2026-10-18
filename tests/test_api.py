"""Tests for the API client."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import aiohttp
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.printlog.api import (
    NOT_MODIFIED,
    PrintLogAuthError,
    PrintLogClient,
    PrintLogConnectionError,
    PrintLogNotFoundError,
    PrintLogRateLimitError,
    PrintLogUnsupportedError,
    PrintLogValidationError,
    normalized_host,
)

BASE = "https://api.example.test"
PROBLEM = {"Content-Type": "application/problem+json"}


@pytest.fixture
def client(hass, aioclient_mock):
    return PrintLogClient(async_get_clientsession(hass), BASE, "secret-key")


async def test_sends_api_key_header_not_query(aioclient_mock, client) -> None:
    aioclient_mock.get(f"{BASE}/api/Users/me", json={"id": 7, "displayName": "Chris"})
    me = await client.get_me()
    assert me["id"] == 7
    method, url, _data, headers = aioclient_mock.mock_calls[0]
    assert headers["X-Api-Key"] == "secret-key"
    assert "api_key" not in str(url).lower()
    assert "secret-key" not in str(url)


async def test_version_returns_version_and_etag(aioclient_mock, client) -> None:
    aioclient_mock.get(
        f"{BASE}/api/sync/version", json={"version": "v9"}, headers={"ETag": 'W/"a"'}
    )
    assert await client.get_version(None) == ("v9", 'W/"a"')
    assert "If-None-Match" not in aioclient_mock.mock_calls[0][3]


async def test_version_304_returns_not_modified(aioclient_mock, client) -> None:
    aioclient_mock.get(f"{BASE}/api/sync/version", status=304)
    assert await client.get_version('W/"a"') is NOT_MODIFIED
    assert aioclient_mock.mock_calls[0][3]["If-None-Match"] == 'W/"a"'


async def test_snapshot_passes_timezone(aioclient_mock, client) -> None:
    aioclient_mock.get(
        f"{BASE}/api/sync/snapshot", json={"printers": []}, headers={"ETag": 'W/"s"'}
    )
    data, etag = await client.get_snapshot("America/Chicago", None)
    assert data == {"printers": []}
    assert etag == 'W/"s"'
    assert aioclient_mock.mock_calls[0][1].query["timeZone"] == "America/Chicago"


async def test_snapshot_304(aioclient_mock, client) -> None:
    aioclient_mock.get(f"{BASE}/api/sync/snapshot", status=304)
    assert await client.get_snapshot("UTC", 'W/"s"') is NOT_MODIFIED


async def test_unread_count(aioclient_mock, client) -> None:
    body = {"unreadCount": 2, "unreadAchievementCount": 1}
    aioclient_mock.get(f"{BASE}/api/Notifications/unread-count", json=body)
    assert await client.get_unread_count() == body


@pytest.mark.parametrize(
    ("status", "headers", "body", "exc"),
    [
        (401, {}, None, PrintLogAuthError),
        (403, {}, None, PrintLogAuthError),
        (429, {"Retry-After": "30"}, None, PrintLogRateLimitError),
        (400, PROBLEM, {"detail": "x"}, PrintLogValidationError),
        (404, {}, None, PrintLogNotFoundError),
        (500, {}, None, PrintLogConnectionError),
        (503, {}, None, PrintLogConnectionError),
    ],
)
async def test_status_mapping(aioclient_mock, client, status, headers, body, exc) -> None:
    aioclient_mock.get(
        f"{BASE}/api/Users/me", status=status, headers=headers, json=body
    ) if body is not None else aioclient_mock.get(
        f"{BASE}/api/Users/me", status=status, headers=headers
    )
    with pytest.raises(exc) as info:
        await client.get_me()
    if exc is PrintLogRateLimitError:
        assert info.value.retry_after == 30
    if exc is PrintLogValidationError:
        assert "x" in str(info.value)


async def test_rate_limit_without_header(aioclient_mock, client) -> None:
    aioclient_mock.get(f"{BASE}/api/Users/me", status=429)
    with pytest.raises(PrintLogRateLimitError) as info:
        await client.get_me()
    assert info.value.retry_after is None


async def test_validation_uses_title_when_no_detail(aioclient_mock, client) -> None:
    aioclient_mock.post(
        f"{BASE}/api/PrinterMaintenance",
        status=400,
        headers=PROBLEM,
        json={"title": "Bad thing"},
    )
    with pytest.raises(PrintLogValidationError, match="Bad thing"):
        await client.create_maintenance(1, datetime.now(UTC), None, "d", None)


async def test_timeout_is_connection_error(aioclient_mock, client) -> None:
    aioclient_mock.get(f"{BASE}/api/Users/me", exc=TimeoutError())
    with pytest.raises(PrintLogConnectionError):
        await client.get_me()


async def test_client_error_is_connection_error(aioclient_mock, client) -> None:
    aioclient_mock.get(f"{BASE}/api/Users/me", exc=aiohttp.ClientConnectionError())
    with pytest.raises(PrintLogConnectionError):
        await client.get_me()


SPOOL = "11111111-1111-1111-1111-111111111111"


async def test_adjust_remaining(aioclient_mock, client) -> None:
    aioclient_mock.post(
        f"{BASE}/api/Filaments/{SPOOL}/adjustments",
        json={"beforeGrams": 100, "afterGrams": 90},
    )
    result = await client.adjust_remaining(SPOOL, "homeassistant", -10, "n")
    assert result == {"beforeGrams": 100, "afterGrams": 90}
    assert aioclient_mock.mock_calls[0][2] == {
        "source": "homeassistant",
        "delta": -10,
        "notes": "n",
    }


async def test_set_spool_active(aioclient_mock, client) -> None:
    aioclient_mock.put(f"{BASE}/api/Filaments/{SPOOL}/active", status=204)
    assert await client.set_spool_active(SPOOL, False) is None
    assert aioclient_mock.mock_calls[0][2] == {"isActive": False}


@pytest.mark.parametrize("which", ["adjust", "active"])
async def test_new_endpoint_404_is_unsupported(aioclient_mock, client, which) -> None:
    if which == "adjust":
        aioclient_mock.post(f"{BASE}/api/Filaments/{SPOOL}/adjustments", status=404)
        call = client.adjust_remaining(SPOOL, "homeassistant", 1, None)
    else:
        aioclient_mock.put(f"{BASE}/api/Filaments/{SPOOL}/active", status=404)
        call = client.set_spool_active(SPOOL, True)
    with pytest.raises(PrintLogUnsupportedError):
        await call


@pytest.mark.parametrize("which", ["adjust", "active"])
async def test_new_endpoint_problem_404_is_not_found(aioclient_mock, client, which) -> None:
    kwargs = {"status": 404, "headers": PROBLEM, "json": {"title": "Not Found"}}
    if which == "adjust":
        aioclient_mock.post(f"{BASE}/api/Filaments/{SPOOL}/adjustments", **kwargs)
        call = client.adjust_remaining(SPOOL, "homeassistant", 1, None)
    else:
        aioclient_mock.put(f"{BASE}/api/Filaments/{SPOOL}/active", **kwargs)
        call = client.set_spool_active(SPOOL, True)
    with pytest.raises(PrintLogNotFoundError):
        await call


async def test_create_maintenance_body(aioclient_mock, client) -> None:
    aioclient_mock.post(f"{BASE}/api/PrinterMaintenance", status=201, json={"id": 5})
    when = datetime(2026, 10, 17, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
    result = await client.create_maintenance(101, when, "Nozzle", "Swapped", "n")
    assert result == {"id": 5}
    body = aioclient_mock.mock_calls[0][2]
    assert body == {
        "printerId": 101,
        "done": True,
        "date": "2026-10-17T12:00:00-05:00",
        "category": "Nozzle",
        "description": "Swapped",
        "notes": "n",
    }
    json.dumps(body)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://API.3dprintlog.com/", "api.3dprintlog.com"),
        ("http://localhost:5101", "localhost:5101"),
        ("https://x:443", "x"),
    ],
)
def test_normalized_host(url, expected) -> None:
    assert normalized_host(url) == expected
