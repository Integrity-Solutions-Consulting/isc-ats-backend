"""DELETE /auth/me must revoke an active marketing consent (marketing-consent Slice 3).

Covers:
- A candidate with an active marketing consent self-deletes -> the row is
  revoked with revoked_source="account_close", and terms_privacy is left
  active/untouched (design D3/D5: account closure never revokes legal
  acceptance).
- A candidate with NO active marketing consent self-deletes -> no error,
  no-op on the consent step.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import create_access_token, hash_password
from app.main import app
from app.modules.auth.application.consents_service import (
    CURRENT_POLICY_VERSION,
    ConsentsService,
)
from app.modules.auth.infrastructure.models import (
    CONSENT_MARKETING,
    CONSENT_TERMS_PRIVACY,
    Consent,
    User,
)
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.candidates_repository import CandidateRepository


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _candidate_portal_id(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None, "user_portal:candidate must be seeded"
    return portal.id


async def _make_candidate_user(session: AsyncSession) -> User:
    portal_id = await _candidate_portal_id(session)
    return await UserRepository(session).add(
        User(
            email=f"cand-{uuid.uuid4().hex[:12]}@test.example.com",
            password_hash=hash_password("Pass1234!"),
            portal_id=portal_id,
            email_verified=True,
        )
    )


async def _make_candidate_profile(session: AsyncSession, user: User) -> Candidate:
    candidate = Candidate(user_id=user.id, first_name="Test", last_name="Candidate")
    return await CandidateRepository(session).add(candidate)


def _bearer(user_id: int, portal: str) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _active_row(session: AsyncSession, user_id: int, consent_type: str) -> Consent | None:
    stmt = select(Consent).where(
        Consent.user_id == user_id,
        Consent.consent_type == consent_type,
        Consent.revoked_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def test_delete_me_revokes_active_marketing_consent(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)
    await _make_candidate_profile(session, user)
    service = ConsentsService(session)
    await service.grant(
        user.id,
        CONSENT_MARKETING,
        policy_version=CURRENT_POLICY_VERSION,
        source="profile_modal",
    )
    await service.grant(
        user.id,
        CONSENT_TERMS_PRIVACY,
        policy_version=CURRENT_POLICY_VERSION,
        source="registration",
    )

    response = await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "candidate"))
    assert response.status_code == 204

    marketing = await _active_row(session, user.id, CONSENT_MARKETING)
    assert marketing is None

    stmt = select(Consent).where(
        Consent.user_id == user.id, Consent.consent_type == CONSENT_MARKETING
    )
    revoked = (await session.execute(stmt)).scalar_one()
    assert revoked.revoked_at is not None
    assert revoked.revoked_source == "account_close"

    terms = await _active_row(session, user.id, CONSENT_TERMS_PRIVACY)
    assert terms is not None
    assert terms.revoked_at is None


async def test_delete_me_no_active_marketing_consent_is_noop(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)
    await _make_candidate_profile(session, user)
    # Deliberately no marketing consent at all.

    response = await client.delete("/api/v1/auth/me", headers=_bearer(user.id, "candidate"))

    assert response.status_code == 204
    marketing = await _active_row(session, user.id, CONSENT_MARKETING)
    assert marketing is None
