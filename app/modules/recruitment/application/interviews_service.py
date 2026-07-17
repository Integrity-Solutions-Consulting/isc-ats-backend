from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

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
        interviewer_id = changes.get("interviewer_id", interview.interviewer_id)
        if ("scheduled_at" in changes or "ends_at" in changes or "interviewer_id" in changes) and scheduled is not None and ends is not None:
            await self._assert_no_double_booking(
                interviewer_id, scheduled, ends, exclude_id=interview_id
            )
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
        now_utc = datetime.now(UTC)
        stmt = (
            select(Interview)
            .join(Application, Application.id == Interview.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(Candidate.user_id == user_id)
            .where(Interview.status_id == offered.id)
            .where(Interview.scheduled_at.is_(None))
            .where(Interview.is_active.is_(True))
            # Expired offers (token_expires_at in the past) are no longer
            # confirmable, so they must not surface as open offers.
            .where(
                or_(
                    Interview.token_expires_at.is_(None),
                    Interview.token_expires_at >= now_utc,
                )
            )
            .order_by(Interview.id)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def list_scheduled_for_candidate(self, user_id: int) -> list[Interview]:
        """Scheduled interviews owned by user_id, with scheduled_at set.

        Any status with a real scheduled_at counts here — Mode A (status=scheduled,
        HR picked the time directly) and Mode B (status=confirmed, candidate picked
        from HR's offered slots) must both surface, since scheduled_at only ever
        gets set once a time is actually locked in. cancelled is explicitly
        excluded (mirrors get_agenda in interviews_routes.py) so a cancelled
        interview never reappears as "my interview" just because scheduled_at was
        left set on the row.
        """
        session = self.repository.session
        param_repo = ParameterRepository(session)
        cancelled = await param_repo.get_by_type_and_code("interview_status", "cancelled")
        cancelled_id: int | None = cancelled.id if cancelled is not None else None

        stmt = (
            select(Interview)
            .join(Application, Application.id == Interview.application_id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(Candidate.user_id == user_id)
            .where(Interview.scheduled_at.is_not(None))
            .where(Interview.is_active.is_(True))
            .order_by(Interview.scheduled_at)
        )
        if cancelled_id is not None:
            stmt = stmt.where(Interview.status_id != cancelled_id)
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

        # The offer must still be in status=offered. Otherwise an HR-cancelled
        # (or otherwise transitioned) interview would still be treated as an open
        # offer the candidate could confirm — scheduled_at being None is not
        # sufficient to prove the offer is live.
        offered = await ParameterRepository(
            self.repository.session
        ).get_by_type_and_code("interview_status", "offered")
        if offered is None or interview.status_id != offered.id:
            raise InterviewOfferClosedError(
                "This interview offer is no longer open"
            )

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
        interviewer. On success: sets scheduled_at/ends_at, status=confirmed,
        scheduler=candidate.
        """
        interview = await self.get_offer_for_candidate(interview_id, user_id)

        try:
            start = datetime.fromisoformat(str(chosen_slot["start"]))
            end = datetime.fromisoformat(str(chosen_slot["end"]))
        except (KeyError, ValueError) as exc:
            raise InterviewValidationError(
                "chosen_slot must have valid 'start' and 'end' ISO-8601 datetimes"
            ) from exc

        # The candidate-supplied slot must carry an explicit timezone offset — a
        # naive datetime is ambiguous and would let a client's local time be
        # silently reinterpreted as UTC.
        if start.tzinfo is None or end.tzinfo is None:
            raise InterviewValidationError(
                "chosen_slot 'start' and 'end' must be timezone-aware ISO-8601 datetimes"
            )

        # Reject slots that start in the past — a confirmed interview must be in
        # the future.
        if start < datetime.now(UTC):
            raise InterviewValidationError(
                "chosen_slot 'start' must be in the future"
            )

        offered = interview.offered_slots or []
        matched = False
        for slot in offered:
            try:
                s_start = datetime.fromisoformat(str(slot.get("start")))
                s_end = datetime.fromisoformat(str(slot.get("end")))
            except (KeyError, ValueError, TypeError):
                continue
            if s_start.tzinfo is None:
                s_start = s_start.replace(tzinfo=UTC)
            if s_end.tzinfo is None:
                s_end = s_end.replace(tzinfo=UTC)
            if s_start == start and s_end == end:
                matched = True
                break

        if not matched:
            raise InterviewValidationError(
                "chosen_slot must match one of the offered_slots"
            )

        await self._assert_no_double_booking(
            interview.interviewer_id, start, end, exclude_id=interview.id
        )

        session = self.repository.session
        param_repo = ParameterRepository(session)
        confirmed_param = await param_repo.get_by_type_and_code("interview_status", "confirmed")
        if confirmed_param is None:
            raise InterviewReferenceError(
                "Parameter (interview_status, confirmed) not found — run seed migration"
            )
        candidate_param = await param_repo.get_by_type_and_code(
            "interview_scheduler", "candidate"
        )
        if candidate_param is None:
            raise InterviewReferenceError(
                "Parameter (interview_scheduler, candidate) not found — run seed migration"
            )

        confirmed = await self.repository.update(
            interview,
            {
                "scheduled_at": start,
                "ends_at": end,
                "status_id": confirmed_param.id,
                "scheduled_by_id": candidate_param.id,
            },
        )

        # D4: notify the offer's creator (HR) that the candidate picked a slot —
        # atomically with the status change, same dual-channel pattern as
        # create_invite (in-app here; the interviewer's own email + notification
        # stays unchanged in the route-layer background task, notify_slot_confirmed).
        #
        # Fallback: when created_by is null (legacy rows), notify interviewer_id
        # instead — someone must be told.
        # Dedup: when the offer's recorded creator IS the interviewer, skip here —
        # notify_slot_confirmed already notifies the interviewer on every confirm,
        # so adding one here too would double it up for the same recipient. The
        # fallback case above does NOT count as "creator == interviewer" — the
        # creator is simply unknown, so the interviewer notification here is the
        # only one that will ever be sent for that recipient.
        creator_id = confirmed.created_by
        if creator_id is not None and creator_id == confirmed.interviewer_id:
            pass
        else:
            recipient_id = creator_id if creator_id is not None else confirmed.interviewer_id
            session.add(
                Notification(
                    recipient_id=recipient_id,
                    title="Un candidato eligió su horario",
                    body="El candidato eligió un horario para su entrevista.",
                    related_entity_type="interview",
                    related_entity_id=confirmed.id,
                    created_by=None,
                )
            )

        return confirmed

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
        # A cancelled interview must NOT block a slot even though its row is still
        # active (not soft-deleted) — mirrors AvailableSlotsService.get_slots.
        cancelled_param = await ParameterRepository(session).get_by_type_and_code(
            "interview_status", "cancelled"
        )
        cancelled_id = cancelled_param.id if cancelled_param is not None else None
        stmt = (
            select(Interview)
            .where(Interview.interviewer_id == interviewer_id)
            .where(Interview.is_active.is_(True))
            .where(Interview.scheduled_at.is_not(None))
            .where(Interview.scheduled_at < end)
            .where(Interview.ends_at > start)
        )
        if cancelled_id is not None:
            stmt = stmt.where(Interview.status_id != cancelled_id)
        if exclude_id is not None:
            stmt = stmt.where(Interview.id != exclude_id)

        result = await session.execute(stmt)
        conflict = result.scalar_one_or_none()
        if conflict is not None:
            raise InterviewDoubleBookingError(
                f"Interviewer {interviewer_id} already has an interview "
                f"overlapping [{start.isoformat()}, {end.isoformat()})"
            )
