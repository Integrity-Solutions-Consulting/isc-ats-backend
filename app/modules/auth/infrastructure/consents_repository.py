from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Row, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import Consent, User
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.shared.repository import BaseRepository


class ConsentsRepository(BaseRepository[Consent]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Consent)

    async def has_ever_decided(self, user_id: int, consent_type: str) -> bool:
        """True if the user has any consent row of this type — grant, refusal, or
        revoked — regardless of whether it is currently active."""
        stmt = select(
            exists().where(
                Consent.user_id == user_id,
                Consent.consent_type == consent_type,
            )
        )
        return bool((await self.session.execute(stmt)).scalar_one())

    async def is_currently_active(self, user_id: int, consent_type: str) -> bool:
        """True if the user has a currently-active (non-revoked) row of this type.

        A refusal row (accepted_at == revoked_at) is never active, since
        revoked_at is set from the moment it is inserted.
        """
        stmt = select(
            exists().where(
                Consent.user_id == user_id,
                Consent.consent_type == consent_type,
                Consent.revoked_at.is_(None),
            )
        )
        return bool((await self.session.execute(stmt)).scalar_one())

    async def grant(
        self,
        user_id: int,
        consent_type: str,
        *,
        policy_version: str,
        source: str,
        ip_address: str | None = None,
        accepted_at: datetime | None = None,
    ) -> Consent:
        """Revoke any currently-active row of this type, then insert a fresh one.

        Revoke-then-insert (rather than upsert/update) is what makes this
        collision-safe against the partial unique index on
        (user_id, consent_type) WHERE revoked_at IS NULL: the revoking UPDATE
        is flushed before the new row is inserted, so the index never sees two
        active rows for the same user+type at once.
        """
        await self.revoke(user_id, consent_type, source=source)
        consent = Consent(
            user_id=user_id,
            consent_type=consent_type,
            accepted_at=accepted_at or datetime.now(UTC),
            policy_version=policy_version,
            source=source,
            ip_address=ip_address,
        )
        return await self.add(consent)

    async def record_refusal(
        self,
        user_id: int,
        consent_type: str,
        *,
        policy_version: str,
        source: str,
        ip_address: str | None = None,
    ) -> Consent:
        """Insert a row proving the user was asked and declined.

        accepted_at and revoked_at are set to the SAME instant, computed once,
        so there is no microsecond gap that would make the row look briefly
        active.
        """
        now = datetime.now(UTC)
        consent = Consent(
            user_id=user_id,
            consent_type=consent_type,
            accepted_at=now,
            revoked_at=now,
            policy_version=policy_version,
            source=source,
            ip_address=ip_address,
        )
        return await self.add(consent)

    async def revoke(
        self, user_id: int, consent_type: str, *, source: str | None = None
    ) -> bool:
        """Revoke the currently-active row for this user+type, if any.

        Returns False (no-op, not an error) when there is nothing to revoke.
        """
        stmt = select(Consent).where(
            Consent.user_id == user_id,
            Consent.consent_type == consent_type,
            Consent.revoked_at.is_(None),
        )
        active = (await self.session.execute(stmt)).scalar_one_or_none()
        if active is None:
            return False
        active.revoked_at = datetime.now(UTC)
        active.revoked_source = source
        await self.session.flush()
        return True

    async def list_active_marketing_subscribers(self) -> Sequence[Row]:
        """Active marketing-consent candidates, newest consent first.

        Used by the marketing export (Slice 4) — implemented now as part of the
        foundation contract.
        """
        stmt = (
            select(
                Candidate.first_name,
                Candidate.last_name,
                User.email,
                Consent.accepted_at,
            )
            .join(User, (User.id == Consent.user_id) & User.is_active.is_(True))
            .join(Candidate, (Candidate.user_id == User.id) & Candidate.is_active.is_(True))
            .where(Consent.consent_type == "marketing")
            .where(Consent.revoked_at.is_(None))
            .where(Consent.is_active.is_(True))
            .order_by(Consent.accepted_at.desc())
        )
        return (await self.session.execute(stmt)).all()
