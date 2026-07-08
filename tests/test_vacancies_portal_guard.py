"""Regression: candidate-portal tokens must never reach staff-only vacancy endpoints.

CRITICAL BOLA: a candidate holding the coarse ``recruitment.vacancies.read``
permission could GET ``/vacancies/{id}/pipeline`` (plus ``/documents``, the full
``GET /vacancies/{id}``, the lists and ``/generate-poster``) and read every
applicant's name, salary expectation and match score across all clients.

``forbid_candidate_portal`` must reject candidate tokens even WHEN they hold the
permission, so a future permission change can't reopen the leak. ``/stages`` — the
only vacancy endpoint a candidate legitimately needs — must stay reachable.
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
    CANDIDATE_ROLE_NAME,
    assign_role_to_user,
    bootstrap_admin,
)
from app.modules.auth.infrastructure.models import Permission, Role, RolePermission, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

BASE = "/api/v1/recruitment/vacancies"

# Every vacancy endpoint that exposes cross-candidate / client data. A candidate
# token must be rejected on all of these regardless of RBAC.
STAFF_ONLY_PATHS = [
    "",  # list vacancies (client info, non-public vacancies)
    "/expanded",  # list expanded (client info)
    "/1",  # full VacancyRead (client_company_id, contact_id)
    "/1/pipeline",  # every applicant's PII / salary / match score
    "/1/documents",  # every applicant's generated Word docs
    "/1/generate-poster",
]


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


async def _candidate_with_vacancies_read(session: AsyncSession) -> int:
    """Bootstrap RBAC + a candidate explicitly granted the coarse staff permission.

    Granting ``recruitment.vacancies.read`` to the candidate is the abuse case:
    it makes ``require_permission`` pass so the test proves the PORTAL guard —
    not the permission check — is what blocks the request.
    """
    await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None
    cand = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@cand.local", portal_id=portal.id)
    )
    cand_role = (
        await session.execute(
            select(Role)
            .where(Role.name == CANDIDATE_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, cand.id, cand_role.id)

    perm = (
        await session.execute(
            select(Permission).where(Permission.code == "recruitment.vacancies.read")
        )
    ).scalar_one()
    # Idempotent: bootstrap already grants this code to the candidate role today,
    # and after the least-privilege split it won't — either way the grant must be
    # present so require_permission passes and the PORTAL guard is what's tested.
    stmt = (
        pg_insert(RolePermission)
        .values(role_id=cand_role.id, permission_id=perm.id, is_active=True)
        .on_conflict_do_update(
            index_elements=[RolePermission.role_id, RolePermission.permission_id],
            set_={"is_active": True},
        )
    )
    await session.execute(stmt)
    await session.flush()
    return cand.id


@pytest.mark.parametrize("path", STAFF_ONLY_PATHS)
async def test_candidate_forbidden_on_staff_vacancy_endpoints(
    client: AsyncClient, session: AsyncSession, path: str
) -> None:
    cand_id = await _candidate_with_vacancies_read(session)
    res = await client.get(BASE + path, headers=_bearer(cand_id, portal="candidate"))
    assert res.status_code == 403, f"{path} leaked to candidate portal"
    assert "Staff-only" in res.json()["detail"]


async def test_candidate_can_still_reach_vacancy_stages(
    client: AsyncClient, session: AsyncSession
) -> None:
    """/stages omits client/contact and is the one endpoint a candidate needs."""
    cand_id = await _candidate_with_vacancies_read(session)
    res = await client.get(
        f"{BASE}/1/stages", headers=_bearer(cand_id, portal="candidate")
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)


async def test_staff_not_blocked_on_pipeline(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The portal guard must not affect staff tokens."""
    result = await bootstrap_admin(
        session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret"
    )
    res = await client.get(
        f"{BASE}/1/pipeline", headers=_bearer(result.user_id, portal="staff")
    )
    assert res.status_code != 403
