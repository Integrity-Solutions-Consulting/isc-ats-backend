"""Registration must capture terms/privacy + marketing consent (marketing-consent Slice 2).

Fixes a live bug: the "Crea tu cuenta" form validated the mandatory terms
checkbox client-side but never persisted the decision anywhere. Covers:
- accepts_terms=False is rejected with 422 before any DB work.
- A successful registration always writes exactly one active terms_privacy
  consent row, source="registration".
- accepts_marketing=False writes ZERO marketing consent rows (D3 anti-conflation
  — unchecked means "not decided yet", not "declined"). This is the single
  highest-value test in this slice.
- accepts_marketing=True writes exactly one active marketing consent row.
- Same-transaction atomicity: a failure between the User insert and the
  consent insert must roll back the whole registration, leaving no user and
  no consent row behind.
- Reactivation re-affirms terms_privacy (revoke-then-insert, D1's safety net)
  without raising a unique-violation, exercised end-to-end through the real
  register -> deactivate -> re-register flow (not just at the repository
  unit level, which Slice 1 already covers).
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
from app.modules.auth.application.auth_service import AuthService
from app.modules.auth.application.bootstrap_service import (
    ensure_candidate_role,
    grant_candidate_permissions_to_role,
    sync_permissions,
)
from app.modules.auth.application.consents_service import (
    CURRENT_POLICY_VERSION,
    ConsentsService,
)
from app.modules.auth.infrastructure.consents_repository import ConsentsRepository
from app.modules.auth.infrastructure.models import (
    CONSENT_MARKETING,
    CONSENT_TERMS_PRIVACY,
    Consent,
    User,
)
from app.modules.auth.infrastructure.repository import (
    RefreshTokenRepository,
    UserRepository,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

_PASSWORD = "StrongPass123!"


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
    """Swallows enqueued tasks so registration never triggers real SMTP here."""

    async def enqueue(self, task_name: str, *args: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_task_queue() -> None:
    app.state.task_queue = _NoOpTaskQueue()


async def _bootstrap_candidate_role(session: AsyncSession) -> None:
    await sync_permissions(session)
    role = await ensure_candidate_role(session)
    await grant_candidate_permissions_to_role(session, role.id)


def _bearer(user_id: int, portal: str) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Schema-level validation
# ---------------------------------------------------------------------------


async def test_register_rejects_declined_terms(client: AsyncClient) -> None:
    email = f"terms-{uuid.uuid4().hex[:12]}@test.example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PASSWORD, "accepts_terms": False},
    )
    assert response.status_code == 422


async def test_register_rejects_missing_terms_field(client: AsyncClient) -> None:
    email = f"terms-{uuid.uuid4().hex[:12]}@test.example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PASSWORD},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# terms_privacy — always written on a successful registration
# ---------------------------------------------------------------------------


async def test_successful_registration_writes_one_active_terms_privacy_row(
    client: AsyncClient, session: AsyncSession
) -> None:
    email = f"terms-{uuid.uuid4().hex[:12]}@test.example.com"

    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PASSWORD, "accepts_terms": True},
    )
    assert response.status_code == 201

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one()
    rows = (
        await session.execute(
            select(Consent).where(
                Consent.user_id == user.id, Consent.consent_type == CONSENT_TERMS_PRIVACY
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.revoked_at is None
    assert row.source == "registration"
    assert row.policy_version == CURRENT_POLICY_VERSION


# ---------------------------------------------------------------------------
# marketing — the D3 anti-conflation contract
# ---------------------------------------------------------------------------


async def test_registration_with_marketing_unchecked_writes_zero_marketing_rows(
    client: AsyncClient, session: AsyncSession
) -> None:
    """CRITICAL (D3): accepts_marketing=False must insert NOTHING — no refusal
    row, no marketing row at all. An unchecked box means "not decided yet",
    not "declined". A refusal row here would make the Slice-3 profile modal
    never appear for any new user, silently."""
    email = f"mkt-off-{uuid.uuid4().hex[:12]}@test.example.com"

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": _PASSWORD,
            "accepts_terms": True,
            "accepts_marketing": False,
        },
    )
    assert response.status_code == 201

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one()
    has_any = await ConsentsRepository(session).has_ever_decided(user.id, CONSENT_MARKETING)
    assert has_any is False, "no marketing row of any kind may exist when box was unchecked"


async def test_registration_with_marketing_unchecked_by_default(
    client: AsyncClient, session: AsyncSession
) -> None:
    """accepts_marketing omitted entirely must behave exactly like False (default)."""
    email = f"mkt-default-{uuid.uuid4().hex[:12]}@test.example.com"

    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PASSWORD, "accepts_terms": True},
    )
    assert response.status_code == 201

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one()
    rows = (
        await session.execute(
            select(Consent).where(
                Consent.user_id == user.id, Consent.consent_type == CONSENT_MARKETING
            )
        )
    ).scalars().all()
    assert rows == []


async def test_registration_with_marketing_checked_writes_one_active_row(
    client: AsyncClient, session: AsyncSession
) -> None:
    email = f"mkt-on-{uuid.uuid4().hex[:12]}@test.example.com"

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": _PASSWORD,
            "accepts_terms": True,
            "accepts_marketing": True,
        },
    )
    assert response.status_code == 201

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one()
    rows = (
        await session.execute(
            select(Consent).where(
                Consent.user_id == user.id, Consent.consent_type == CONSENT_MARKETING
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].revoked_at is None
    assert rows[0].source == "registration"


# ---------------------------------------------------------------------------
# same-transaction atomicity (R1)
# ---------------------------------------------------------------------------


async def test_registration_rolls_back_entirely_when_consent_grant_fails(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the consent grant fails mid-registration, the caller rolling back the
    session must leave neither the user nor any consent row behind — proving
    the User/UserRole/Consent inserts share one atomic unit of work (no
    intermediate commit exists inside register_candidate to break on).

    Exercised at the service layer (not HTTP): the test client override used
    elsewhere in this suite bypasses get_session's commit-on-success /
    rollback-on-exception wrapper, so an HTTP-level failure wouldn't actually
    prove atomicity here. Calling register_candidate directly and rolling
    back this test's own session on failure is the faithful equivalent of
    what get_session does in production.
    """
    await _bootstrap_candidate_role(session)
    email = f"atomic-{uuid.uuid4().hex[:12]}@test.example.com"

    service = AuthService(
        UserRepository(session),
        RefreshTokenRepository(session),
        ParameterRepository(session),
        ConsentsService(session),
    )

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated failure after the User insert")

    monkeypatch.setattr(ConsentsService, "grant", _boom)

    with pytest.raises(RuntimeError):
        await service.register_candidate(
            email, _PASSWORD, "127.0.0.1", accepts_terms=True
        )
    await session.rollback()

    user_count = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    assert user_count is None, "no user row may survive a failed registration"

    consent_count = (
        await session.execute(
            select(Consent).join(User, User.id == Consent.user_id).where(User.email == email)
        )
    ).scalar_one_or_none()
    assert consent_count is None, "no consent row may survive a failed registration"


# ---------------------------------------------------------------------------
# reactivation — D1's revoke-then-insert safety net, exercised end-to-end
# ---------------------------------------------------------------------------


async def test_reactivation_re_grants_terms_privacy_without_conflict(
    client: AsyncClient, session: AsyncSession
) -> None:
    """register -> deactivate -> re-register with the same email must not raise
    a unique-violation, and must leave exactly one ACTIVE terms_privacy row
    (the original is revoked, a fresh one inserted) — the D1 safety net
    actually working through the real HTTP flow, not just the repository."""
    await _bootstrap_candidate_role(session)
    email = f"reactivate-{uuid.uuid4().hex[:12]}@test.example.com"

    first = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PASSWORD, "accepts_terms": True},
    )
    assert first.status_code == 201

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one()

    deactivate = await client.delete(
        "/api/v1/auth/me", headers=_bearer(user.id, "candidate")
    )
    assert deactivate.status_code == 204

    second = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "AnotherStrongPass1!", "accepts_terms": True},
    )
    assert second.status_code == 201

    rows = (
        await session.execute(
            select(Consent).where(
                Consent.user_id == user.id, Consent.consent_type == CONSENT_TERMS_PRIVACY
            )
        )
    ).scalars().all()
    active = [r for r in rows if r.revoked_at is None]
    revoked = [r for r in rows if r.revoked_at is not None]
    assert len(active) == 1, "exactly one ACTIVE terms_privacy row after reactivation"
    assert len(revoked) == 1, "the original grant must be revoked, not duplicated"
