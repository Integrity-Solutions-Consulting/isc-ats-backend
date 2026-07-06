from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.shared.repository import BaseRepository


class CandidateRepository(BaseRepository[Candidate]):
    """Repository for recruitment.candidates — adds the unique-field lookups."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Candidate)

    async def get_by_user_id(self, user_id: int) -> Candidate | None:
        stmt = (
            select(Candidate)
            .where(Candidate.user_id == user_id)
            .where(Candidate.is_active.is_(True))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_cedula(self, cedula: str) -> Candidate | None:
        # No is_active filter: the uq_candidates_cedula constraint spans every
        # row, so the duplicate guard must see inactive (closed-account) rows
        # too — otherwise the insert fails with a raw IntegrityError (500)
        # instead of a clean DuplicateCandidateError (409).
        stmt = select(Candidate).where(Candidate.cedula == cedula)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def deactivate_by_user_id(self, user_id: int) -> None:
        """Logically deactivate the candidate profile linked to user_id (no-op if absent)."""
        candidate = await self.get_by_user_id(user_id)
        if candidate is not None:
            candidate.is_active = False
            await self.session.flush()

    async def reactivate_by_user_id(self, user_id: int) -> None:
        """Reactivate the candidate profile linked to user_id.

        No-op unless there is a deactivated profile (get_by_user_id filters to
        active rows, so it cannot find one to switch back on). Called after a
        returning candidate confirms reactivation via the email link.
        """
        stmt = (
            select(Candidate)
            .where(Candidate.user_id == user_id)
            .where(Candidate.is_active.is_(False))
        )
        candidate = (await self.session.execute(stmt)).scalar_one_or_none()
        if candidate is not None:
            candidate.is_active = True
            await self.session.flush()
