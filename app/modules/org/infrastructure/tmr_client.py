"""TMR client adapter — reads client/company data from the external .NET system.

Mirrors the shape/testability of CloudflareTurnstileVerifier: an injectable
httpx transport keeps the adapter unit-testable without real network. TMR uses a
short-lived (~15 min) HS256 JWT obtained from /api/auth/login; the refresh token
is ignored entirely — when the cached access token is missing or near expiry we
simply log in again.

A TMR outage (any transport error, non-2xx, unexpected body, or a 401 that a
single re-login can't fix) surfaces as TmrUnavailableError so the caller can apply
its fail-safe fallback and never break the client dropdown.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

# Re-login when the cached token is within this margin of its expiry, so an
# in-flight request never rides an about-to-expire token.
_TOKEN_REFRESH_MARGIN = timedelta(seconds=30)


@dataclass(frozen=True)
class TmrClient:
    """A single client/company fetched from TMR, mapped to isc-ats fields.

    TMR `id` -> external_id, `nombreComercial` -> name, `activo` -> is_active.
    """

    external_id: int
    name: str
    is_active: bool


class TmrUnavailableError(Exception):
    """TMR could not be reached or returned an unusable response.

    Distinct from a normal empty result — the caller treats this as "keep the
    last mirrored data" rather than "TMR has no clients".
    """


class TmrApiClient:
    """HTTP adapter over TMR's REST API (login + clients list)."""

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        timeout: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user = user
        self._password = password
        self._timeout = timeout
        # Injectable transport keeps the adapter testable without real network.
        self._transport = transport
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
        )

    async def login(self) -> None:
        """POST credentials, cache the access token and its parsed expiry.

        Any transport error, non-2xx status, `success` false, or a missing/
        malformed token or expiry raises TmrUnavailableError.
        """
        payload = {"user": self._user, "password": self._password}
        try:
            async with self._client() as client:
                response = await client.post("/api/auth/login", json=payload)
        except httpx.HTTPError as exc:
            raise TmrUnavailableError(f"TMR login request failed: {exc}") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise TmrUnavailableError(
                f"TMR login returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise TmrUnavailableError("TMR login returned a non-JSON body") from exc

        if not isinstance(body, dict) or body.get("success") is not True:
            raise TmrUnavailableError("TMR login was not successful")

        data = body.get("data")
        if not isinstance(data, dict):
            raise TmrUnavailableError("TMR login response is missing 'data'")

        token = data.get("accessToken")
        expires_at_raw = data.get("expiresAt")
        if not token or not isinstance(token, str):
            raise TmrUnavailableError("TMR login response is missing 'accessToken'")

        self._token = token
        self._token_expires_at = _parse_expires_at(expires_at_raw)

    async def _valid_token(self) -> str:
        """Return a usable token, re-logging-in when missing or near expiry."""
        if self._token is None or self._token_is_stale():
            await self.login()
        # login() either set a token or raised; assert for the type checker.
        assert self._token is not None
        return self._token

    def _token_is_stale(self) -> bool:
        if self._token_expires_at is None:
            return True
        return datetime.now(UTC) >= (self._token_expires_at - _TOKEN_REFRESH_MARGIN)

    async def fetch_clients(self) -> list[TmrClient]:
        """GET /api/clientes with a Bearer token; map the array into TmrClients.

        On a 401, re-login once and retry (covers a token that expired server-side
        earlier than its advertised `expiresAt`). Any transport error, 5xx, a
        second 401, or a non-list body raises TmrUnavailableError.
        """
        token = await self._valid_token()
        response = await self._get_clientes(token)

        if response.status_code == httpx.codes.UNAUTHORIZED:
            # One forced re-login + retry, then give up.
            self._token = None
            self._token_expires_at = None
            token = await self._valid_token()
            response = await self._get_clientes(token)
            if response.status_code == httpx.codes.UNAUTHORIZED:
                raise TmrUnavailableError("TMR clients rejected the token (401)")

        if response.status_code < 200 or response.status_code >= 300:
            raise TmrUnavailableError(
                f"TMR clients returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise TmrUnavailableError("TMR clients returned a non-JSON body") from exc

        if not isinstance(body, list):
            raise TmrUnavailableError("TMR clients response was not a JSON array")

        return [
            mapped
            for element in body
            if (mapped := _map_client(element)) is not None
        ]

    async def _get_clientes(self, token: str) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with self._client() as client:
                return await client.get("/api/clientes", headers=headers)
        except httpx.HTTPError as exc:
            raise TmrUnavailableError(f"TMR clients request failed: {exc}") from exc


def _parse_expires_at(raw: object) -> datetime:
    """Parse TMR's ISO-8601 UTC `expiresAt` into a timezone-aware datetime."""
    if not isinstance(raw, str) or not raw:
        raise TmrUnavailableError("TMR login response is missing 'expiresAt'")
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TmrUnavailableError(f"TMR 'expiresAt' is not ISO-8601: {raw}") from exc
    # Treat a naive timestamp as UTC (TMR documents expiresAt as UTC).
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _map_client(element: object) -> TmrClient | None:
    """Map one TMR array element to a TmrClient, defensively.

    Returns None (skip) when the element isn't an object or has no usable id.
    A missing name becomes an empty string; `activo` is coerced to bool.
    """
    if not isinstance(element, dict):
        return None
    external_id = element.get("id")
    if not isinstance(external_id, int) or isinstance(external_id, bool):
        return None
    name = element.get("nombreComercial")
    return TmrClient(
        external_id=external_id,
        name=name if isinstance(name, str) else "",
        is_active=bool(element.get("activo")),
    )
