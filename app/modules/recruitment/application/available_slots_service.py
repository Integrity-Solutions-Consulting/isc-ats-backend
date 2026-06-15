"""Service for available interview slots and interviewer listing.

Separates slot-computation from DB access: reads availability windows and
existing booked interviews, delegates computation to SlotGenerationService.
"""

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.application.slot_generation_service import (
    AvailabilityWindow,
    SlotGenerationService,
)
from app.modules.recruitment.infrastructure.interview_models import (
    Interview,
    InterviewerAvailability,
)
from app.shared.repository import BaseRepository


class AvailableSlotsService:
    """Read-only service: available slots + interviewer listing."""

    def __init__(
        self,
        availability_repo: BaseRepository[InterviewerAvailability],
        interview_repo: BaseRepository[Interview],
    ) -> None:
        self.availability_repo = availability_repo
        self.interview_repo = interview_repo

    async def get_slots(
        self,
        *,
        interviewer_id: int,
        target_date: date,
    ) -> list[datetime]:
        """Return free UTC datetimes for `interviewer_id` on `target_date`.

        Only active availability rows are considered.
        Only active, non-cancelled interviews block slots.
        """
        session: AsyncSession = self.availability_repo.session

        # Load active availability windows for this interviewer
        stmt = (
            select(InterviewerAvailability)
            .where(InterviewerAvailability.user_id == interviewer_id)
            .where(InterviewerAvailability.is_active.is_(True))
        )
        avail_rows = list((await session.execute(stmt)).scalars().all())

        windows = [
            AvailabilityWindow(
                user_id=row.user_id,
                day_of_week=row.day_of_week,
                start_time=row.start_time,
                end_time=row.end_time,
                slot_duration_min=row.slot_duration_min,
                buffer_min=row.buffer_min,
                is_active=row.is_active,
            )
            for row in avail_rows
        ]

        # Load booked interviews on target_date for this interviewer (active, not cancelled).
        # REQ-04: a cancelled interview (status_id resolves to interview_status/cancelled)
        # must NOT block a slot, even when the row is still active (not soft-deleted).
        # We resolve the cancelled status_id via ParameterRepository to avoid hardcoding.
        param_repo = ParameterRepository(session)
        cancelled_param = await param_repo.get_by_type_and_code("interview_status", "cancelled")
        cancelled_id: int | None = cancelled_param.id if cancelled_param is not None else None

        day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=UTC)
        day_end = day_start.replace(hour=23, minute=59, second=59)

        booked_stmt = (
            select(Interview)
            .where(Interview.interviewer_id == interviewer_id)
            .where(Interview.is_active.is_(True))
            .where(Interview.scheduled_at.is_not(None))
            .where(Interview.scheduled_at >= day_start)
            .where(Interview.scheduled_at <= day_end)
        )
        if cancelled_id is not None:
            booked_stmt = booked_stmt.where(Interview.status_id != cancelled_id)
        booked_rows = list((await session.execute(booked_stmt)).scalars().all())
        booked_intervals = [
            (row.scheduled_at, row.ends_at)
            for row in booked_rows
            if row.scheduled_at is not None and row.ends_at is not None
        ]

        svc = SlotGenerationService()
        return svc.get_available_slots(
            target_date=target_date,
            windows=windows,
            booked_interviews=booked_intervals,
        )

    async def get_interviewers(self) -> list[User]:
        """Return distinct User objects that have at least one active availability row.

        No role-name filter is applied — any user with active availability qualifies.
        """
        session: AsyncSession = self.availability_repo.session

        # Subquery: user_ids with active availability
        subq = (
            select(InterviewerAvailability.user_id)
            .where(InterviewerAvailability.is_active.is_(True))
            .distinct()
            .subquery()
        )
        stmt = (
            select(User)
            .where(User.id.in_(select(subq)))
            .where(User.is_active.is_(True))
            .order_by(User.id)
        )
        return list((await session.execute(stmt)).scalars().all())
