"""Sync-on-read mirror of TMR clients into org.client_companies.

Fetches the live client list from TMR and upserts it keyed on `external_id` using
an idempotent ON CONFLICT DO UPDATE — safe under concurrent workers. Two safety
rails wrap the fetch:

* Throttle — a per-process timestamp of the last *successful* sync with a 60s TTL.
  Within the TTL, sync() returns immediately without touching TMR. Multiple workers
  each syncing once per TTL is fine because the upsert is idempotent.
* Fallback — a TMR outage (TmrUnavailableError) is logged and swallowed, never
  propagated, so a TMR failure can't break the client dropdown / form.
"""

import logging
import time
from collections.abc import Callable
from typing import ClassVar

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.modules.org.infrastructure.models import ClientCompany
from app.modules.org.infrastructure.tmr_client import (
    TmrApiClient,
    TmrClient,
    TmrUnavailableError,
)

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 60.0


class ClientSyncService:
    """Mirrors TMR clients into org.client_companies on a throttled, fail-safe basis."""

    # Per-process (shared across instances) timestamp of the last successful sync.
    # A class attribute rather than an instance one so every request/worker sees the
    # same throttle window regardless of how the service is constructed.
    _last_success_at: ClassVar[float | None] = None

    def __init__(
        self,
        tmr: TmrApiClient,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tmr = tmr
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    @classmethod
    def reset_throttle(cls) -> None:
        """Clear the shared throttle timestamp (used by tests)."""
        cls._last_success_at = None

    def _throttled(self) -> bool:
        last = type(self)._last_success_at
        if last is None:
            return False
        return (self._clock() - last) < self._ttl_seconds

    async def sync(self, session: AsyncSession) -> None:
        """Fetch from TMR and upsert; throttled and fail-safe.

        Returns without touching TMR when a successful sync happened within the TTL.
        A TMR outage is logged and swallowed — never raised to the caller.
        """
        if self._throttled():
            return

        try:
            clients = await self._tmr.fetch_clients()
        except TmrUnavailableError:
            # A TMR outage must never break the caller — keep the last mirrored data.
            logger.warning("TMR client sync skipped: TMR is unavailable", exc_info=True)
            return

        await self._upsert(session, clients)
        # Only a successful sync arms the throttle, so an outage retries next call.
        type(self)._last_success_at = self._clock()

    async def _upsert(
        self, session: AsyncSession, clients: list[TmrClient]
    ) -> None:
        if not clients:
            return
        rows = [
            {
                "external_id": c.external_id,
                "name": c.name,
                "is_active": c.is_active,
            }
            for c in clients
        ]
        stmt = pg_insert(ClientCompany).values(rows)
        # Infer the partial unique index (WHERE external_id IS NOT NULL); on conflict
        # refresh the mirrored fields. updated_at is set explicitly because ORM's
        # onupdate does not fire on a Core INSERT ... ON CONFLICT statement.
        stmt = stmt.on_conflict_do_update(
            index_elements=[ClientCompany.external_id],
            index_where=text("external_id IS NOT NULL"),
            set_={
                "name": stmt.excluded.name,
                "is_active": stmt.excluded.is_active,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)


def build_client_sync_service(app_settings: Settings) -> ClientSyncService:
    """Construct a ClientSyncService + its TMR adapter from settings."""
    tmr = TmrApiClient(
        base_url=app_settings.tmr_base_url,
        user=app_settings.tmr_user,
        password=app_settings.tmr_password,
    )
    return ClientSyncService(tmr)


# Process-wide singleton so the TMR token cache and the throttle window persist
# across requests. Built lazily; safe to leave unbuilt when TMR is disabled.
_service_singleton: ClientSyncService | None = None


def get_client_sync_service() -> ClientSyncService:
    """Return the process-wide sync service, building it from settings once."""
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = build_client_sync_service(settings)
    return _service_singleton
