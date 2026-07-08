"""Candidate self-registration must carry a cédula/document number.

The frontend onboarding form requires it, but the API is the only real gate — a
script (the pentester did exactly this) can skip the form and POST a profile with
no cédula. Staff are exempt: they create candidates during manual entry / the TMR
integration, where the document may be filled in later.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import (
    CANDIDATE_ROLE_NAME,
    assign_role_to_user,
    bootstrap_admin,
)
from app.modules.auth.infrastructure.models import Role, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

URL = "/api/v1/recruitment/candidates"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


class _NoOpTaskQueue:
    async def enqueue(self, task_name: str, *args: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_task_queue() -> None:
    app.state.task_queue = _NoOpTaskQueue()


def _bearer(user_id: int, portal: str) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _new_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None
    return await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@cand.local", portal_id=portal.id)
    )


async def _candidate_user(session: AsyncSession) -> User:
    """Bootstrap RBAC + a candidate user holding the candidate role."""
    await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    user = await _new_user(session)
    role = (
        await session.execute(
            select(Role)
            .where(Role.name == CANDIDATE_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, user.id, role.id)
    await session.flush()
    return user


async def test_candidate_registration_requires_cedula(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _candidate_user(session)
    res = await client.post(
        URL,
        json={"user_id": user.id, "first_name": "Ana", "last_name": "Diaz"},
        headers=_bearer(user.id, portal="candidate"),
    )
    assert res.status_code == 422
    assert "cédula" in res.json()["detail"].lower()


async def test_candidate_registration_accepts_with_cedula(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _candidate_user(session)
    res = await client.post(
        URL,
        json={
            "user_id": user.id,
            "first_name": "Ana",
            "last_name": "Diaz",
            "doc_type": "passport",
            "cedula": uuid.uuid4().hex[:10],
        },
        headers=_bearer(user.id, portal="candidate"),
    )
    assert res.status_code == 201


async def test_staff_can_create_candidate_without_cedula(
    client: AsyncClient, session: AsyncSession
) -> None:
    result = await bootstrap_admin(
        session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret"
    )
    target = await _new_user(session)
    await session.flush()
    res = await client.post(
        URL,
        json={"user_id": target.id, "first_name": "Staff", "last_name": "Made"},
        headers=_bearer(result.user_id, portal="staff"),
    )
    assert res.status_code == 201
