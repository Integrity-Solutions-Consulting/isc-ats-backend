"""Migration smoke tests for auth.consents (cac9a09107f0, 99c299cb8503).

Verifies at the DB level:
  (a) the partial unique index actually rejects a second active row for the
      same user+type — this is the collision-safety guarantee the whole
      feature's revoke-then-insert design rests on.
  (b) the backfill gave every existing candidate-portal user exactly one
      terms_privacy row sourced from 'backfill', with accepted_at matching
      their own created_at (not now()), and zero marketing rows.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.modules.auth.infrastructure.models import CONSENT_MARKETING, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository

_INSERT_ACTIVE_CONSENT_SQL = (
    "INSERT INTO auth.consents "
    "(user_id, consent_type, accepted_at, policy_version, source, is_active, created_at) "
    "VALUES (:uid, :ctype, now(), '1.0', 'banner', true, now())"
)


async def _make_candidate_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "candidate")
    assert portal is not None
    return await UserRepository(session).add(
        User(
            email=f"migsmoke-{uuid.uuid4().hex[:12]}@test.example.com",
            portal_id=portal.id,
            email_verified=True,
        )
    )


async def test_partial_unique_index_rejects_second_active_row(session: AsyncSession) -> None:
    user = await _make_candidate_user(session)
    params = {"uid": user.id, "ctype": CONSENT_MARKETING}

    await session.execute(text(_INSERT_ACTIVE_CONSENT_SQL), params)
    await session.flush()

    with pytest.raises(IntegrityError):
        await session.execute(text(_INSERT_ACTIVE_CONSENT_SQL), params)
        await session.flush()


async def test_backfill_gave_every_candidate_exactly_one_terms_privacy_row() -> None:
    # Independent engine/session so this test sees data committed by the
    # migration itself, outside the rolled-back `session` fixture's transaction.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        missing_or_extra = (
            await s.execute(
                text(
                    """
                    SELECT u.id
                    FROM auth.users u
                    JOIN org.parameters p ON p.id = u.portal_id
                    LEFT JOIN auth.consents c
                        ON c.user_id = u.id AND c.consent_type = 'terms_privacy'
                    WHERE p.type = 'user_portal' AND p.code = 'candidate'
                    GROUP BY u.id
                    HAVING COUNT(c.id) != 1
                    """
                )
            )
        ).all()
        assert missing_or_extra == []

        mismatched_accepted_at = (
            await s.execute(
                text(
                    """
                    SELECT u.id
                    FROM auth.users u
                    JOIN auth.consents c
                        ON c.user_id = u.id
                        AND c.consent_type = 'terms_privacy'
                        AND c.source = 'backfill'
                    WHERE c.accepted_at != u.created_at
                    """
                )
            )
        ).all()
        assert mismatched_accepted_at == []

        marketing_rows = (
            await s.execute(
                text("SELECT COUNT(*) FROM auth.consents WHERE consent_type = 'marketing'")
            )
        ).scalar_one()
        assert marketing_rows == 0
    await engine.dispose()
