"""Defense-in-depth: candidate-portal tokens never reach application-documents.

These documents are staff-only and not row-scoped per candidate. Even if a
candidate were granted recruitment.application_documents.read, forbid_candidate_portal
must still reject them — so a future permission change can't turn into an IDOR.
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
from app.modules.auth.infrastructure.models import Permission, Role, RolePermission, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

LIST_URL = "/api/v1/recruitment/application-documents"
_PERMISSION = "recruitment.application_documents.read"


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


async def test_candidate_with_permission_still_forbidden(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Sync the permission catalog + candidate role.
    await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

    # Candidate user + candidate role.
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None
    cand = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@cand.local", portal_id=portal.id)
    )
    cand_role = (
        await session.execute(
            select(Role).where(Role.name == CANDIDATE_ROLE_NAME).where(Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, cand.id, cand_role.id)

    # Explicitly grant the staff permission to the candidate role (the abuse case).
    perm = (
        await session.execute(select(Permission).where(Permission.code == _PERMISSION))
    ).scalar_one()
    session.add(RolePermission(role_id=cand_role.id, permission_id=perm.id, is_active=True))
    await session.flush()

    # require_permission passes now, but the portal guard must still reject.
    res = await client.get(LIST_URL, headers=_bearer(cand.id, portal="candidate"))
    assert res.status_code == 403
    assert "Staff-only" in res.json()["detail"]
