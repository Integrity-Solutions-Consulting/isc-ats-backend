"""Tests for the TMR client sync service against the real local test DB.

Assertions are scoped by the external_ids inserted here so the 9 pre-existing
legacy rows (and anything else in org.client_companies) never interfere.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.application.client_companies_service import ClientCompanyService
from app.modules.org.application.client_sync_service import ClientSyncService
from app.modules.org.infrastructure.models import ClientCompany
from app.modules.org.infrastructure.tmr_client import TmrClient, TmrUnavailableError
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

# High external_ids unlikely to collide with anything real in the DB.
EXT_NEW = 990001
EXT_EXISTING = 990002
EXT_FALLBACK = 990003
EXT_THROTTLE = 990004
EXT_FILTER = 990010


class _StubTmr:
    """Stub TMR client exposing the fetch_clients() contract the service depends on."""

    def __init__(
        self, clients: list[TmrClient] | None = None, *, error: bool = False
    ) -> None:
        self._clients = clients or []
        self._error = error
        self.calls = 0

    async def fetch_clients(self) -> list[TmrClient]:
        self.calls += 1
        if self._error:
            raise TmrUnavailableError("TMR down")
        return list(self._clients)


async def _fetch_rows(session: AsyncSession, external_id: int) -> list[ClientCompany]:
    result = await session.execute(
        select(ClientCompany).where(ClientCompany.external_id == external_id)
    )
    return list(result.scalars().all())


async def test_sync_inserts_new_client(session: AsyncSession) -> None:
    ClientSyncService.reset_throttle()
    stub = _StubTmr([TmrClient(external_id=EXT_NEW, name="NEW CO", is_active=True)])

    await ClientSyncService(stub, ttl_seconds=0.0).sync(session)

    rows = await _fetch_rows(session, EXT_NEW)
    assert len(rows) == 1
    assert rows[0].name == "NEW CO"
    assert rows[0].is_active is True
    assert rows[0].external_id == EXT_NEW


async def test_sync_updates_existing_client_without_duplicating(
    session: AsyncSession,
) -> None:
    ClientSyncService.reset_throttle()
    session.add(
        ClientCompany(name="OLD NAME", external_id=EXT_EXISTING, is_active=False)
    )
    await session.flush()

    stub = _StubTmr(
        [TmrClient(external_id=EXT_EXISTING, name="UPDATED NAME", is_active=True)]
    )
    await ClientSyncService(stub, ttl_seconds=0.0).sync(session)

    # pg_insert updated the row via raw SQL; expire the identity map so the ORM
    # re-reads the fresh values rather than returning cached attributes.
    session.expire_all()
    rows = await _fetch_rows(session, EXT_EXISTING)
    assert len(rows) == 1  # updated in place, not duplicated
    assert rows[0].name == "UPDATED NAME"
    assert rows[0].is_active is True


async def test_sync_swallows_unavailable_and_leaves_table_unchanged(
    session: AsyncSession,
) -> None:
    ClientSyncService.reset_throttle()
    stub = _StubTmr(error=True)

    # Must not raise — a TMR outage is a no-op for the caller.
    await ClientSyncService(stub, ttl_seconds=0.0).sync(session)

    count = (
        await session.execute(
            select(func.count())
            .select_from(ClientCompany)
            .where(ClientCompany.external_id == EXT_FALLBACK)
        )
    ).scalar_one()
    assert count == 0
    assert stub.calls == 1


async def test_throttle_skips_second_call_within_ttl(session: AsyncSession) -> None:
    ClientSyncService.reset_throttle()
    now = [1000.0]
    stub = _StubTmr(
        [TmrClient(external_id=EXT_THROTTLE, name="THROTTLED", is_active=True)]
    )
    service = ClientSyncService(stub, ttl_seconds=60.0, clock=lambda: now[0])

    await service.sync(session)
    assert stub.calls == 1

    now[0] = 1030.0  # still within the 60s TTL
    await service.sync(session)
    assert stub.calls == 1  # skipped — no second TMR call

    now[0] = 1070.0  # past the TTL
    await service.sync(session)
    assert stub.calls == 2  # TMR called again after the window elapsed


async def test_external_only_filter_hides_local_rows(session: AsyncSession) -> None:
    # One purely-local row (external_id NULL) and one TMR-sourced row.
    session.add(ClientCompany(name="LOCAL ONLY", is_active=True))
    session.add(ClientCompany(name="FROM TMR", external_id=EXT_FILTER, is_active=True))
    await session.flush()

    service = ClientCompanyService(BaseRepository(session, ClientCompany))
    params = PageParams(page=1, size=100)

    external_items, _ = await service.list(params, external_only=True)
    all_items, _ = await service.list(params, external_only=False)

    # external_only hides every NULL-external_id row (including the 9 legacy rows).
    assert all(item.external_id is not None for item in external_items)
    assert EXT_FILTER in {item.external_id for item in external_items}
    # Default behaviour still returns local rows.
    assert any(item.external_id is None for item in all_items)
