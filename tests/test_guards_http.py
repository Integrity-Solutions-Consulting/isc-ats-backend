"""End-to-end smoke test that require_permission is actually wired on the routes.

Overrides get_session so the ASGI app reuses the rolled-back test session, which
already contains the bootstrapped admin (flushed, not committed).
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import bootstrap_admin
from app.modules.auth.infrastructure.models import User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

GUARDED_URL = "/api/v1/org/departments"

# Staff-only endpoint (requires auth.roles.create — candidates must NOT access it).
ROLES_CREATE_URL = "/api/v1/auth/roles"
# Endpoint a bootstrapped candidate IS allowed to call.
VACANCIES_LIST_URL = "/api/v1/recruitment/vacancies"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _bearer(user_id: int, portal: str = "staff") -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _make_candidate_user(session: AsyncSession) -> User:
    """Create a candidate-portal user (no roles assigned)."""
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None, "user_portal:candidate must be seeded"
    user = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@cand.local", portal_id=portal.id)
    )
    return user


async def test_guarded_route_rejects_missing_token(client: AsyncClient) -> None:
    response = await client.get(GUARDED_URL)
    assert response.status_code == 401  # HTTPBearer rejects the missing credential


async def test_guarded_route_forbids_user_without_permission(
    client: AsyncClient, session: AsyncSession
) -> None:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )

    response = await client.get(GUARDED_URL, headers=_bearer(user.id))

    assert response.status_code == 403
    assert "org.departments.read" in response.json()["detail"]


async def test_guarded_route_allows_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

    response = await client.get(GUARDED_URL, headers=_bearer(admin.user_id))

    assert response.status_code == 200
    assert "items" in response.json()


async def test_candidate_cannot_access_staff_only_endpoint(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A candidate token must NOT be able to call POST /auth/roles (auth.roles.create).

    This test fails before the fix because of the portal=="candidate" bypass.
    """
    user = await _make_candidate_user(session)

    response = await client.post(
        ROLES_CREATE_URL,
        json={"name": "evil-role"},
        headers=_bearer(user.id, portal="candidate"),
    )

    assert response.status_code == 403


async def test_candidate_can_access_allowed_endpoint(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A candidate with the bootstrapped candidate role gets 200 on GET /recruitment/vacancies."""
    # Bootstrap gives the candidate role recruitment.vacancies.read.
    admin = await bootstrap_admin(
        session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret"
    )

    # Create a candidate-portal user and explicitly assign the candidate role.
    from sqlalchemy import select
    from app.modules.auth.application.bootstrap_service import CANDIDATE_ROLE_NAME
    from app.modules.auth.application.bootstrap_service import assign_role_to_user
    from app.modules.auth.infrastructure.models import Role

    cand_user = await _make_candidate_user(session)
    cand_role = (
        await session.execute(
            select(Role)
            .where(Role.name == CANDIDATE_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, cand_user.id, cand_role.id)

    response = await client.get(
        VACANCIES_LIST_URL,
        headers=_bearer(cand_user.id, portal="candidate"),
    )

    assert response.status_code == 200
    assert "items" in response.json()
