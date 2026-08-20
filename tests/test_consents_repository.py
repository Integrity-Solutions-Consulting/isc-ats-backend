"""Repository tests for auth.consents (marketing-consent Slice 1).

The single most important test here is the D2 anti-conflation test: a
refusal (accepted_at == revoked_at) must count as "already decided" but NOT
as "currently active" — the whole feature's collision-safety and consent-
history semantics depend on the repository never conflating "never asked"
with "asked and declined".
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.consents_repository import ConsentsRepository
from app.modules.auth.infrastructure.models import (
    CONSENT_MARKETING,
    Consent,
    User,
)
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidate_models import Candidate

POLICY_VERSION = "1.0"


async def _make_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None
    return await UserRepository(session).add(
        User(
            email=f"consent-{uuid.uuid4().hex[:12]}@test.example.com",
            portal_id=portal.id,
            email_verified=True,
        )
    )


async def test_refusal_counts_as_decided_but_not_active(session: AsyncSession) -> None:
    """D2 anti-conflation: a refusal is a decision, but not an active consent."""
    user = await _make_user(session)
    repo = ConsentsRepository(session)

    await repo.record_refusal(
        user.id, CONSENT_MARKETING, policy_version=POLICY_VERSION, source="banner"
    )

    assert await repo.has_ever_decided(user.id, CONSENT_MARKETING) is True
    assert await repo.is_currently_active(user.id, CONSENT_MARKETING) is False


async def test_grant_called_twice_is_revoke_then_insert(session: AsyncSession) -> None:
    """grant() must not IntegrityError on a re-grant — exactly one active row survives."""
    user = await _make_user(session)
    repo = ConsentsRepository(session)

    await repo.grant(user.id, CONSENT_MARKETING, policy_version=POLICY_VERSION, source="banner")
    await repo.grant(user.id, CONSENT_MARKETING, policy_version=POLICY_VERSION, source="banner")

    stmt = select(Consent).where(
        Consent.user_id == user.id, Consent.consent_type == CONSENT_MARKETING
    )
    rows = (await session.execute(stmt)).scalars().all()
    active = [r for r in rows if r.revoked_at is None]
    revoked = [r for r in rows if r.revoked_at is not None]
    assert len(active) == 1
    assert len(revoked) == 1


async def test_record_refusal_sets_accepted_at_equal_revoked_at(session: AsyncSession) -> None:
    user = await _make_user(session)
    repo = ConsentsRepository(session)

    consent = await repo.record_refusal(
        user.id, CONSENT_MARKETING, policy_version=POLICY_VERSION, source="banner"
    )
    assert consent.accepted_at == consent.revoked_at


async def test_revoke_with_no_active_row_returns_false(session: AsyncSession) -> None:
    user = await _make_user(session)
    repo = ConsentsRepository(session)

    result = await repo.revoke(user.id, CONSENT_MARKETING, source="account_close")
    assert result is False


async def test_revoke_sets_revoked_source(session: AsyncSession) -> None:
    user = await _make_user(session)
    repo = ConsentsRepository(session)
    await repo.grant(user.id, CONSENT_MARKETING, policy_version=POLICY_VERSION, source="banner")

    result = await repo.revoke(user.id, CONSENT_MARKETING, source="account_close")
    assert result is True

    stmt = select(Consent).where(
        Consent.user_id == user.id, Consent.consent_type == CONSENT_MARKETING
    )
    row = (await session.execute(stmt)).scalar_one()
    assert row.revoked_source == "account_close"
    assert row.revoked_at is not None


async def test_list_active_marketing_subscribers_only_returns_active(
    session: AsyncSession,
) -> None:
    repo = ConsentsRepository(session)

    active_user = await _make_user(session)
    revoked_user = await _make_user(session)
    refused_user = await _make_user(session)

    for u, tag in (
        (active_user, "active"),
        (revoked_user, "revoked"),
        (refused_user, "refused"),
    ):
        session.add(Candidate(user_id=u.id, first_name="Test", last_name=tag))
    await session.flush()

    await repo.grant(
        active_user.id, CONSENT_MARKETING, policy_version=POLICY_VERSION, source="banner"
    )

    await repo.grant(
        revoked_user.id, CONSENT_MARKETING, policy_version=POLICY_VERSION, source="banner"
    )
    await repo.revoke(revoked_user.id, CONSENT_MARKETING, source="banner")

    await repo.record_refusal(
        refused_user.id, CONSENT_MARKETING, policy_version=POLICY_VERSION, source="banner"
    )

    rows = await repo.list_active_marketing_subscribers()
    emails = {r.email for r in rows}
    assert active_user.email in emails
    assert revoked_user.email not in emails
    assert refused_user.email not in emails
