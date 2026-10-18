"""Async client for the 3D Print Log API. Pure: no Home Assistant imports."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import REQUEST_TIMEOUT


class _NotModified:
    """Sentinel type for a 304 response."""

    def __repr__(self) -> str:
        return "NOT_MODIFIED"


NOT_MODIFIED = _NotModified()


class PrintLogError(Exception):
    """Base class for API client errors."""


class PrintLogAuthError(PrintLogError):
    """The API key was rejected (401 or 403)."""


class PrintLogRateLimitError(PrintLogError):
    """The API returned 429."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("Rate limited")
        self.retry_after = retry_after


class PrintLogNotFoundError(PrintLogError):
    """The resource was not found (404)."""


class PrintLogValidationError(PrintLogError):
    """The API rejected the request (400)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PrintLogConnectionError(PrintLogError):
    """Timeout, connection failure or a 5xx response."""


class PrintLogUnsupportedError(PrintLogError):
    """The API is too old to have this endpoint."""


def normalized_host(base_url: str) -> str:
    """Return a lowercase host[:port] for a base URL, dropping default ports.

    IPv6 hosts keep their brackets. Raises ValueError for an invalid port.
    """
    parts = urlsplit(base_url.strip())
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError as err:
        raise ValueError(f"Invalid port in URL: {base_url!r}") from err
    if port is not None and not (
        (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80)
    ):
        return f"{host}:{port}"
    return host


def _parse_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _spool_path(spool_id: str) -> str:
    try:
        return str(uuid.UUID(str(spool_id)))
    except ValueError as err:
        raise PrintLogValidationError("Invalid spool id") from err


class PrintLogClient:
    """Thin client over the 3D Print Log REST API."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        etag: str | None = None,
        new_endpoint: bool = False,
        allow_304: bool = False,
        expect_object: bool = True,
    ) -> tuple[Any, Mapping[str, str]]:
        headers = {"X-Api-Key": self._api_key, "Accept": "application/json"}
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json_body,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                text = await resp.text()
                resp_headers = resp.headers
        except (TimeoutError, aiohttp.ClientError) as err:
            raise PrintLogConnectionError(type(err).__name__) from err

        if status == 304 and allow_304:
            return NOT_MODIFIED, resp_headers
        if status < 300:
            data = _parse_json(text)
            if expect_object and not isinstance(data, dict):
                raise PrintLogConnectionError("Invalid response")
            return data, resp_headers

        body = _parse_json(text)
        if status in (401, 403):
            raise PrintLogAuthError(f"HTTP {status}")
        if status == 429:
            try:
                retry_after: float | None = float(resp_headers.get("Retry-After", ""))
            except ValueError:
                retry_after = None
            if retry_after is not None and (not math.isfinite(retry_after) or retry_after < 0):
                retry_after = None
            raise PrintLogRateLimitError(retry_after)
        if new_endpoint and status == 405:
            raise PrintLogUnsupportedError("Endpoint not available")
        if status == 404:
            if new_endpoint and not (
                isinstance(body, dict) and body.get("code") == "material_not_found"
            ):
                raise PrintLogUnsupportedError("Endpoint not available")
            raise PrintLogNotFoundError("Not found")
        if 400 <= status < 500:
            message = f"HTTP {status}"
            if isinstance(body, dict):
                message = body.get("detail") or body.get("title") or message
            raise PrintLogValidationError(str(message))
        raise PrintLogConnectionError(f"HTTP {status}")

    async def get_me(self) -> dict:
        data, _ = await self._request("GET", "/api/Users/me")
        return data

    async def get_version(self, etag: str | None) -> tuple[str, str | None] | _NotModified:
        data, headers = await self._request("GET", "/api/sync/version", etag=etag, allow_304=True)
        if data is NOT_MODIFIED:
            return NOT_MODIFIED
        version = data.get("version")
        if not isinstance(version, str) or not version:
            raise PrintLogConnectionError("Invalid response")
        return version, headers.get("ETag")

    async def get_snapshot(
        self, time_zone: str, etag: str | None
    ) -> tuple[dict, str | None] | _NotModified:
        data, headers = await self._request(
            "GET",
            "/api/sync/snapshot",
            params={"timeZone": time_zone},
            etag=etag,
            allow_304=True,
        )
        if data is NOT_MODIFIED:
            return NOT_MODIFIED
        return data, headers.get("ETag")

    async def get_unread_count(self) -> dict:
        data, _ = await self._request("GET", "/api/Notifications/unread-count")
        return data

    async def adjust_remaining(
        self, spool_id: str, source: str, delta: float, notes: str | None
    ) -> dict:
        data, _ = await self._request(
            "POST",
            f"/api/Filaments/{_spool_path(spool_id)}/adjustments",
            json_body={"source": source, "delta": delta, "notes": notes},
            new_endpoint=True,
        )
        return data

    async def set_spool_active(self, spool_id: str, is_active: bool) -> None:
        await self._request(
            "PUT",
            f"/api/Filaments/{_spool_path(spool_id)}/active",
            json_body={"isActive": is_active},
            new_endpoint=True,
            expect_object=False,
        )

    async def create_maintenance(
        self,
        printer_id: int,
        date: datetime,
        category: str | None,
        description: str,
        notes: str | None,
    ) -> dict:
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        data, _ = await self._request(
            "POST",
            "/api/PrinterMaintenance",
            json_body={
                "printerId": printer_id,
                "done": True,
                "date": date.isoformat(),
                "category": category,
                "description": description,
                "notes": notes,
            },
        )
        return data
