from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.comms.infrastructure.models import Notification
from app.modules.org.infrastructure.models import Parameter, ProcessStage
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.interviews_schemas import (
    InterviewCreate,
    InterviewInviteCreate,
    InterviewUpdate,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.interview_models import Interview
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

_OFFER_EXPIRY_DAYS = 7


class InterviewNotFoundError(Exception):
    pass


class InterviewReferenceError(Exception):
    """A referenced application, stage, interviewer or parameter does not exist."""


class InterviewValidationError(Exception):
    """The interview window is invalid or the chosen slot is not in offered_slots."""


class InterviewDoubleBookingError(Exception):
    """The interviewer already has an active interview overlapping this time window."""


class InterviewOfferClosedError(Exception):
    """The interview offer is no longer open — it expired or was already confirmed."""


class InterviewService:
    """CRUD + scheduling for recruitment.interviews.

    `interviewer_id` is a user; `status_id` (interview_status) and
    `scheduled_by_id` (interview_scheduler) are org.parameters.
    """

    def __init__(
        self,
        repository: BaseRepository[Interview],
        applications: BaseRepository[Application],
        process_stages: BaseRepository[ProcessStage],
        users: BaseRepository[User],
        parameters: BaseRepository[Parameter],
    ) -> None:
        self.repository = repository
        self.applications = applications
        self.process_stages = process_stages
        self.users = users
        self.parameters = parameters

    async def list(
        self, params: PageParams, *, application_id: int | None = None
    ) -> tuple[list[Interview], int]:
        filters = {"application_id": application_id} if application_id else None
        return await self.repository.list(params, filters=filters)

    async def get(self, interview_id: int) -> Interview:
        interview = await self.repository.get(interview_id)
        if interview is None:
            raise InterviewNotFoundError(f"Interview {interview_id} not found")
        return interview

    async def create(self, data: InterviewCreate, actor: CurrentUser) -> Interview:
        if data.ends_at <= data.scheduled_at:
            raise InterviewValidationError("ends_at must be after scheduled_at")
        await self._validate_refs(data.model_dump())
        await self._assert_no_double_booking(
            data.interviewer_id, data.scheduled_at, data.ends_at
        )
        interview = Interview(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        interview = await self.repository.add(interview)

        # In-app notification for the candidate (symmetric with create_invite)
        candidate_user_id = await self._candidate_user_id(data.application_id)
        if candidate_user_id is not None:
            session = self.repository.session
            session.add(
                Notification(
                    recipient_id=candidate_user_id,
                    title="Tienes una entrevista programada",
                    body="Te han agendado una entrevista. Revisa tu correo para más detalles.",
                    related_entity_type="interview",
                    related_entity_id=interview.id,
                    created_by=actor.user_id,
                    ip_created=actor.ip,
                )
            )
            await session.flush()
        return interview

    async def update(
        self, interview_id: int, data: InterviewUpdate, actor: CurrentUser
    ) -> Interview:
        interview = await self.get(interview_id)
        changes = data.model_dump(exclude_unset=True)
        scheduled = changes.get("scheduled_at", interview.scheduled_at)
        ends = changes.get("ends_at", interview.ends_at)
        if scheduled is not None and ends is not None and ends <= scheduled:
            raise InterviewValidationError("ends_at must be after scheduled_at")
        await self._validate_refs(changes)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(interview, changes)

    async def delete(self, interview_id: int) -> None:
        interview = await self.get(interview_id)
        await self.repository.soft_delete(interview)

    # ── Mode B — candidate self-scheduling ────────────────────────────────────

    async def create_invite(
        self, data: InterviewInviteCreate, actor: CurrentUser
    ) -> Interview:
        """Create a Mode B interview in status=offered the candidate confirms later.

        Validates application, process_stage, and interviewer exist. Resolves
        status_id via interview_status/offered and scheduled_by_id via
        interview_scheduler/hr, and sets an offer expiry. Dual channel: also
        creates an in-app notification for the candidate (the offer email is sent
        best-effort by the route layer).
        """
        await self._assert(self.applications, data.application_id, "application_id")
        await self._assert(self.process_stages, data.process_stage_id, "process_stage_id")
        await self._assert(self.users, data.interviewer_id, "interviewer_id")

        session = self.repository.session
        param_repo = ParameterRepository(session)
        offered_param = await param_repo.get_by_type_and_code("interview_status", "offered")
        if offered_param is None:
            raise InterviewReferenceError(
                "Parameter (interview_status, offered) not found — run seed migration"
            )
        hr_param = await param_repo.get_by_type_and_code("interview_scheduler", "hr")
        if hr_param is None:
            raise InterviewReferenceError(
                "Parameter (interview_scheduler, hr) not found — run seed migration"
            )

        expires_at = datetime.now(UTC) + timedelta(days=_OFFER_EXPIRY_DAYS)

        interview = Interview(
            application_id=data.application_id,
            process_stage_id=data.process_stage_id,
            interviewer_id=data.interviewer_id,
            scheduled_at=None,
            ends_at=None,
            status_id=offered_param.id,
            scheduled_by_id=hr_param.id,
            extra_email=data.extra_email,
            offered_slots=data.offered_slots,
            token_expires_at=expires_at,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        interview = await self.repository.add(interview)

        # Dual channel — the in-app notification is created atomically with the
        # offer (guaranteed); the email is a best-effort second channel fired by
        # the route layer.
        candidate_user_id = await self._candidate_user_id(data.application_id)
        if candidate_user_id is not None:
            session.add(
                Notification(
                    recipient_id=candidate_user_id,
                    title="Tenés horarios para tu entrevista",
                    body="Elegí el horario que mejor te quede para tu entrevista.",
                    related_entity_type="interview",
                    related_entity_id=interview.id,
                    created_by=actor.user_id,
                    ip_created=actor.ip,
                )
            )
            await session.flush()
        return interview

    async def list_offers_for_candidate(self, user_id: int) -> list[Interview]:
        """Open interview offers (status=offered, not yet scheduled) owned by user_id."""
        session = self.repository.session
        offered = await ParameterRepository(session).get_by_type_and_code(
            "interview_status", "offered"
        )
        if offered is None:
            return []
        stmt = (
            select(Interview)
            .join(Application, Application.id == Interview.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(Candidate.user_id == user_id)
            .where(Interview.status_id == offered.id)
            .where(Interview.scheduled_at.is_(None))
            .where(Interview.is_active.is_(True))
            .order_by(Interview.id)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def list_scheduled_for_candidate(self, user_id: int) -> list[Interview]:
        """Scheduled (confirmed) interviews owned by user_id.

        status=scheduled with scheduled_at set — the candidate-facing counterpart
        of list_offers_for_candidate, used to show the confirmed interview after a
        page reload (the offer is gone once confirmed).
        """
        session = self.repository.session
        scheduled = await ParameterRepository(session).get_by_type_and_code(
            "interview_status", "scheduled"
        )
        if scheduled is None:
            return []
        stmt = (
            select(Interview)
            .join(Application, Application.id == Interview.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(Candidate.user_id == user_id)
            .where(Interview.status_id == scheduled.id)
            .where(Interview.scheduled_at.is_not(None))
            .where(Interview.is_active.is_(True))
            .order_by(Interview.scheduled_at)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def get_offer_for_candidate(
        self, interview_id: int, user_id: int
    ) -> Interview:
        """Load an interview offer owned by user_id.

        Raises InterviewNotFoundError (→ 404) when the interview is missing OR not
        owned by user_id — ownership failures never leak existence.
        Raises InterviewOfferClosedError (→ 409) when the offer has expired or was
        already confirmed (scheduled_at set).
        """
        interview = await self.get(interview_id)
        owner_id = await self._candidate_user_id(interview.application_id)
        if owner_id is None or owner_id != user_id:
            raise InterviewNotFoundError(f"Interview {interview_id} not found")

        if interview.scheduled_at is not None:
            raise InterviewOfferClosedError(
                "This interview offer was already confirmed"
            )

        expires = interview.token_expires_at
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires < datetime.now(UTC):
                raise InterviewOfferClosedError("This interview offer has expired")
        return interview

    async def confirm_slot_for_candidate(
        self, interview_id: int, user_id: int, chosen_slot: dict[str, Any]
    ) -> Interview:
        """Candidate confirms a chosen slot from their own open offer.

        Ownership and offer state are enforced by get_offer_for_candidate. Then
        the chosen slot must be one of offered_slots and must not double-book the
        interviewer. On success: sets scheduled_at/ends_at, status=scheduled,
        scheduler=candidate.
        """
        interview = await self.get_offer_for_candidate(interview_id, user_id)

        offered = interview.offered_slots or []
        if chosen_slot not in offered:
            raise InterviewValidationError(
                "chosen_slot must match one of the offered_slots exactly"
            )

        try:
            start = datetime.fromisoformat(str(chosen_slot["start"]))
            end = datetime.fromisoformat(str(chosen_slot["end"]))
        except (KeyError, ValueError) as exc:
            raise InterviewValidationError(
                "chosen_slot must have valid 'start' and 'end' ISO-8601 datetimes"
            ) from exc

        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)

        await self._assert_no_double_booking(
            interview.interviewer_id, start, end, exclude_id=interview.id
        )

        session = self.repository.session
        param_repo = ParameterRepository(session)
        scheduled_param = await param_repo.get_by_type_and_code("interview_status", "scheduled")
        if scheduled_param is None:
            raise InterviewReferenceError(
                "Parameter (interview_status, scheduled) not found — run seed migration"
            )
        candidate_param = await param_repo.get_by_type_and_code(
            "interview_scheduler", "candidate"
        )
        if candidate_param is None:
            raise InterviewReferenceError(
                "Parameter (interview_scheduler, candidate) not found — run seed migration"
            )

        return await self.repository.update(
            interview,
            {
                "scheduled_at": start,
                "ends_at": end,
                "status_id": scheduled_param.id,
                "scheduled_by_id": candidate_param.id,
            },
        )

    async def _candidate_user_id(self, application_id: int) -> int | None:
        """Resolve the user id that owns an application (its candidate's user)."""
        session = self.repository.session
        stmt = (
            select(Candidate.user_id)
            .join(Application, Application.candidate_id == Candidate.id)
            .where(Application.id == application_id)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    # ── private helpers ───────────────────────────────────────────────────────

    async def _validate_refs(self, values: dict[str, Any]) -> None:
        await self._assert(self.applications, values.get("application_id"), "application_id")
        await self._assert(
            self.process_stages, values.get("process_stage_id"), "process_stage_id"
        )
        await self._assert(self.users, values.get("interviewer_id"), "interviewer_id")
        await self._assert(self.parameters, values.get("status_id"), "status_id")
        await self._assert(self.parameters, values.get("scheduled_by_id"), "scheduled_by_id")

    async def _assert(
        self, repo: BaseRepository[Any], entity_id: int | None, label: str
    ) -> None:
        if entity_id is not None and await repo.get(entity_id) is None:
            raise InterviewReferenceError(f"{label}={entity_id} not found")

    async def _assert_no_double_booking(
        self,
        interviewer_id: int,
        start: datetime,
        end: datetime,
        *,
        exclude_id: int | None = None,
    ) -> None:
        """Raise InterviewDoubleBookingError if the interviewer has an active,
        non-soft-deleted interview overlapping [start, end).

        Overlap condition: existing.scheduled_at < end AND existing.ends_at > start
        """
        session = self.repository.session
        stmt = (
            select(Interview)
            .where(Interview.interviewer_id == interviewer_id)
            .where(Interview.is_active.is_(True))
            .where(Interview.scheduled_at.is_not(None))
            .where(Interview.scheduled_at < end)
            .where(Interview.ends_at > start)
        )
        if exclude_id is not None:
            stmt = stmt.where(Interview.id != exclude_id)

        result = await session.execute(stmt)
        conflict = result.scalar_one_or_none()
        if conflict is not None:
            raise InterviewDoubleBookingError(
                f"Interviewer {interviewer_id} already has an interview "
                f"overlapping [{start.isoformat()}, {end.isoformat()})"
            )
