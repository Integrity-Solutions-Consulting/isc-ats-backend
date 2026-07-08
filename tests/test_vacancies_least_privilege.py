"""Least-privilege for the candidate role on vacancy endpoints.

Root cause of the pipeline BOLA was a COARSE permission: the candidate role held
``recruitment.vacancies.read``, which unlocks staff-only sub-resources (pipeline,
documents, client info). The candidate only ever needs stage names, so it should
hold a narrow ``recruitment.vacancies.read_stages`` instead, and ``/stages`` must
accept EITHER code (staff via the broad read, candidate via the narrow one).

Bootstrap must be authoritative: tightening the allowlist has to REVOKE the coarse
grant on the next run, not merely stop adding it.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import (
    CANDIDATE_PERMISSION_CODES,
    CANDIDATE_ROLE_NAME,
    assign_role_to_user,
    bootstrap_admin,
    grant_candidate_permissions_to_role,
)
from app.modules.auth.infrastructure.models import Permission, Role, RolePermission, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

BASE = "/api/v1/recruitment/vacancies"
COARSE = "recruitment.vacancies.read"
NARROW = "recruitment.vacancies.read_stages"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _bearer(user_id: int, portal: str) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _candidate_role(session: AsyncSession) -> Role:
    return (
        await session.execute(
            select(Role)
            .where(Role.name == CANDIDATE_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
    ).scalar_one()


async def _active_role_codes(session: AsyncSession, role_id: int) -> set[str]:
    rows = (
        await session.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
            .where(RolePermission.is_active.is_(True))
        )
    ).scalars().all()
    return set(rows)


def test_allowlist_swaps_coarse_read_for_narrow_read_stages() -> None:
    assert NARROW in CANDIDATE_PERMISSION_CODES
    assert COARSE not in CANDIDATE_PERMISSION_CODES


async def test_bootstrap_grants_candidate_narrow_not_coarse(session: AsyncSession) -> None:
    await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    role = await _candidate_role(session)
    codes = await _active_role_codes(session, role.id)
    assert NARROW in codes
    assert COARSE not in codes


async def test_bootstrap_revokes_stray_coarse_grant(session: AsyncSession) -> None:
    """A candidate role carrying the coarse permission from a previous version must
    lose it when the candidate grant sync re-runs — authoritative allowlist."""
    await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    role = await _candidate_role(session)
    coarse = (
        await session.execute(select(Permission).where(Permission.code == COARSE))
    ).scalar_one()

    # Simulate the legacy grant that today's bootstrap would have left in place.
    await session.execute(
        pg_insert(RolePermission)
        .values(role_id=role.id, permission_id=coarse.id, is_active=True)
        .on_conflict_do_update(
            index_elements=[RolePermission.role_id, RolePermission.permission_id],
            set_={"is_active": True},
        )
    )
    await session.flush()
    assert COARSE in await _active_role_codes(session, role.id)

    await grant_candidate_permissions_to_role(session, role.id)

    assert COARSE not in await _active_role_codes(session, role.id)


async def _make_candidate(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None
    cand = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@cand.local", portal_id=portal.id)
    )
    await assign_role_to_user(session, cand.id, (await _candidate_role(session)).id)
    await session.flush()
    return cand.id


async def test_candidate_reaches_stages_with_narrow_permission_only(
    client: AsyncClient, session: AsyncSession
) -> None:
    """After bootstrap the candidate role holds only read_stages (no coarse read),
    and that alone must let it read /stages."""
    await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    cand_id = await _make_candidate(session)
    res = await client.get(f"{BASE}/1/stages", headers=_bearer(cand_id, portal="candidate"))
    assert res.status_code == 200
    assert isinstance(res.json(), list)


async def test_staff_reaches_stages_via_broad_read(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Staff keep /stages through their existing broad recruitment.vacancies.read —
    no backfill of the new permission required."""
    result = await bootstrap_admin(
        session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret"
    )
    res = await client.get(
        f"{BASE}/1/stages", headers=_bearer(result.user_id, portal="staff")
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)
