"""Unit tests for the TMR HTTP adapter using httpx.MockTransport (no real network)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.modules.org.infrastructure.tmr_client import (
    TmrApiClient,
    TmrClient,
    TmrUnavailableError,
)

BASE = "https://tmr.example.test"


def _expires_in(seconds: float) -> str:
    """An ISO-8601 UTC `expiresAt` `seconds` into the future, TMR's Z-suffix style."""
    return (
        (datetime.now(UTC) + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _login_body(expires_in: float = 900) -> dict:
    return {
        "success": True,
        "data": {
            "accessToken": "tok-abc",
            "refreshToken": "refresh-xyz",
            "expiresAt": _expires_in(expires_in),
            "tokenFamilyId": "fam-1",
            "user": {"id": 1},
        },
    }


def _client(handler, **kwargs) -> TmrApiClient:
    return TmrApiClient(
        base_url=BASE,
        user="svc@example.com",
        password="dummy-pw",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_login_caches_token_and_expiry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/auth/login"
        body = json.loads(request.content)
        assert body == {"user": "svc@example.com", "password": "dummy-pw"}
        return httpx.Response(200, json=_login_body())

    client = _client(handler)
    await client.login()

    assert client._token == "tok-abc"
    assert client._token_expires_at is not None
    assert client._token_expires_at.tzinfo is not None


async def test_fetch_clients_maps_real_payload_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json=_login_body())
        assert request.headers["Authorization"] == "Bearer tok-abc"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 3018,
                    "tipoIdentificacion": "RUC",
                    "numeroIdentificacion": "1111111100001",
                    "nombreComercial": "HOME CLEANER",
                    "email": "x@y.com",
                    "telefono": "",
                    "activo": True,
                },
                {"id": 3019, "nombreComercial": "OTHER CO", "activo": False},
                # Missing nombreComercial -> empty string; activo truthy -> True.
                {"id": 3020, "activo": 1},
                # No id -> skipped defensively.
                {"nombreComercial": "NO ID CO", "activo": True},
            ],
        )

    clients = await _client(handler).fetch_clients()

    assert clients == [
        TmrClient(external_id=3018, name="HOME CLEANER", is_active=True),
        TmrClient(external_id=3019, name="OTHER CO", is_active=False),
        TmrClient(external_id=3020, name="", is_active=True),
    ]


async def test_network_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(TmrUnavailableError):
        await _client(handler).fetch_clients()


async def test_server_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json=_login_body())
        return httpx.Response(503)

    with pytest.raises(TmrUnavailableError):
        await _client(handler).fetch_clients()


async def test_non_list_body_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json=_login_body())
        return httpx.Response(200, json={"success": True})

    with pytest.raises(TmrUnavailableError):
        await _client(handler).fetch_clients()


async def test_login_failure_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "bad creds"})

    with pytest.raises(TmrUnavailableError):
        await _client(handler).fetch_clients()


async def test_401_triggers_exactly_one_relogin_and_retry() -> None:
    login_count = 0
    clientes_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, clientes_count
        if request.url.path == "/api/auth/login":
            login_count += 1
            return httpx.Response(200, json=_login_body())
        clientes_count += 1
        if clientes_count == 1:
            return httpx.Response(401, json={"success": False})
        return httpx.Response(
            200, json=[{"id": 1, "nombreComercial": "A", "activo": True}]
        )

    clients = await _client(handler).fetch_clients()

    assert login_count == 2  # initial login + one forced re-login
    assert clientes_count == 2  # initial call + single retry
    assert clients == [TmrClient(external_id=1, name="A", is_active=True)]


async def test_persistent_401_raises_after_single_retry() -> None:
    login_count = 0
    clientes_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count, clientes_count
        if request.url.path == "/api/auth/login":
            login_count += 1
            return httpx.Response(200, json=_login_body())
        clientes_count += 1
        return httpx.Response(401, json={"success": False})

    with pytest.raises(TmrUnavailableError):
        await _client(handler).fetch_clients()

    assert login_count == 2  # initial + one retry only
    assert clientes_count == 2


async def test_token_reused_within_validity_window() -> None:
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/auth/login":
            login_count += 1
            return httpx.Response(200, json=_login_body(expires_in=900))
        return httpx.Response(200, json=[])

    client = _client(handler)
    await client.fetch_clients()
    await client.fetch_clients()

    assert login_count == 1  # token reused for the second fetch


async def test_relogin_when_token_within_expiry_margin() -> None:
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/auth/login":
            login_count += 1
            # 10s ahead is inside the 30s refresh margin -> treated as stale.
            return httpx.Response(200, json=_login_body(expires_in=10))
        return httpx.Response(200, json=[])

    client = _client(handler)
    await client.fetch_clients()
    await client.fetch_clients()

    assert login_count == 2  # a fresh login happens once the token is near expiry
