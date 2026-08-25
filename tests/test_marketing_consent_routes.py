"""Tests for GET/PUT /auth/me/consents/marketing (marketing-consent Slice 3).

Covers:
- GET for a virgin candidate (never decided) -> {decided: false, subscribed: false}.
- PUT {subscribed: true, source: "profile_modal"} on a virgin user -> 200,
  {decided: true, subscribed: true}, exactly one active row with
  source="profile_modal".
- PUT {subscribed: false, source: "profile_modal"} on a virgin user (the
  "No, gracias" path) -> creates a REFUSAL row (accepted_at == revoked_at),
  {decided: true, subscribed: false}.
- A true -> false -> true toggle cycle -> 3 total rows, exactly 1 active, and
  the recorded source values match the request sequence.
- Staff users get 403 on both verbs.
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
from app.modules.auth.infrastructure.models import CONSENT_MARKETING, Consent, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository


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


async def _staff_portal_id(session: AsyncSession) -> int:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None, "user_portal:staff must be seeded"
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


async def _make_staff_user(session: AsyncSession) -> User:
    portal_id = await _staff_portal_id(session)
    return await UserRepository(session).add(
        User(
            email=f"staff-{uuid.uuid4().hex[:12]}@test.example.com",
            password_hash=hash_password("Pass1234!"),
            portal_id=portal_id,
            email_verified=True,
        )
    )


def _bearer(user_id: int, portal: str) -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _consent_rows(session: AsyncSession, user_id: int) -> list[Consent]:
    stmt = (
        select(Consent)
        .where(Consent.user_id == user_id, Consent.consent_type == CONSENT_MARKETING)
        .order_by(Consent.id)
    )
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


async def test_get_marketing_consent_virgin_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)

    response = await client.get(
        "/api/v1/auth/me/consents/marketing", headers=_bearer(user.id, "candidate")
    )

    assert response.status_code == 200
    assert response.json() == {"decided": False, "subscribed": False}


async def test_get_marketing_consent_staff_forbidden(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_staff_user(session)

    response = await client.get(
        "/api/v1/auth/me/consents/marketing", headers=_bearer(user.id, "staff")
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# PUT — subscribe (grant)
# ---------------------------------------------------------------------------


async def test_put_subscribe_true_on_virgin_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)

    response = await client.put(
        "/api/v1/auth/me/consents/marketing",
        headers=_bearer(user.id, "candidate"),
        json={"subscribed": True, "source": "profile_modal"},
    )

    assert response.status_code == 200
    assert response.json() == {"decided": True, "subscribed": True}

    rows = await _consent_rows(session, user.id)
    active_rows = [row for row in rows if row.revoked_at is None]
    assert len(active_rows) == 1
    assert active_rows[0].source == "profile_modal"


# ---------------------------------------------------------------------------
# PUT — decline (refusal, "No, gracias")
# ---------------------------------------------------------------------------


async def test_put_subscribe_false_on_virgin_user_records_refusal(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)

    response = await client.put(
        "/api/v1/auth/me/consents/marketing",
        headers=_bearer(user.id, "candidate"),
        json={"subscribed": False, "source": "profile_modal"},
    )

    assert response.status_code == 200
    assert response.json() == {"decided": True, "subscribed": False}

    rows = await _consent_rows(session, user.id)
    assert len(rows) == 1
    assert rows[0].accepted_at == rows[0].revoked_at
    assert rows[0].source == "profile_modal"


# ---------------------------------------------------------------------------
# PUT — toggle cycle
# ---------------------------------------------------------------------------


async def test_put_toggle_cycle_true_false_true(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_candidate_user(session)
    headers = _bearer(user.id, "candidate")

    r1 = await client.put(
        "/api/v1/auth/me/consents/marketing",
        headers=headers,
        json={"subscribed": True, "source": "profile_modal"},
    )
    assert r1.json() == {"decided": True, "subscribed": True}

    r2 = await client.put(
        "/api/v1/auth/me/consents/marketing",
        headers=headers,
        json={"subscribed": False, "source": "profile_toggle"},
    )
    assert r2.json() == {"decided": True, "subscribed": False}

    r3 = await client.put(
        "/api/v1/auth/me/consents/marketing",
        headers=headers,
        json={"subscribed": True, "source": "profile_toggle"},
    )
    assert r3.json() == {"decided": True, "subscribed": True}

    rows = await _consent_rows(session, user.id)
    # The toggle-off step (subscribed=false while a row is active) only
    # revokes the existing row in place -- it never inserts (per the route's
    # documented contract: insert only happens through grant()/record_refusal()).
    # So this cycle produces exactly 2 physical rows: the initial grant
    # (revoked by the toggle-off) and the final re-grant.
    assert len(rows) == 2
    active_rows = [row for row in rows if row.revoked_at is None]
    assert len(active_rows) == 1

    # Row 1: granted with profile_modal, then revoked by the toggle-off PUT
    # (revoked_source carries THAT PUT's source, "profile_toggle").
    assert rows[0].source == "profile_modal"
    assert rows[0].revoked_source == "profile_toggle"
    assert rows[0].revoked_at is not None
    # Row 2: the final re-grant, active.
    assert rows[1].source == "profile_toggle"
    assert rows[1].revoked_at is None


async def test_put_marketing_consent_staff_forbidden(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_staff_user(session)

    response = await client.put(
        "/api/v1/auth/me/consents/marketing",
        headers=_bearer(user.id, "staff"),
        json={"subscribed": True, "source": "profile_modal"},
    )

    assert response.status_code == 403
