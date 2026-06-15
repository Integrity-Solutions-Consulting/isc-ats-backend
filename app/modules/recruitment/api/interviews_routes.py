from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import User
from app.modules.comms.application.email_dispatch_service import EmailDispatchService
from app.modules.comms.application.email_sender import EmailMessage
from app.modules.comms.application.email_templates import (
    render_interview_invitation_email,
    render_interview_slot_offer_email,
)
from app.modules.comms.application.meeting_provider import MeetingRequest
from app.modules.comms.infrastructure.email_sender_factory import build_email_sender
from app.modules.comms.infrastructure.meeting_provider_factory import (
    build_meeting_provider,
)
from app.modules.org.infrastructure.models import Parameter, ProcessStage
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.interviews_schemas import (
    InterviewCreate,
    InterviewerRead,
    InterviewInviteCreate,
    InterviewRead,
    InterviewUpdate,
    SlotConfirmRequest,
    SlotRead,
)
from app.modules.recruitment.application.available_slots_service import (
    AvailableSlotsService,
)
from app.modules.recruitment.application.interviews_service import (
    InterviewDoubleBookingError,
    InterviewNotFoundError,
    InterviewOfferClosedError,
    InterviewReferenceError,
    InterviewService,
    InterviewValidationError,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.interview_models import (
    Interview,
    InterviewerAvailability,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/interviews", tags=["recruitment · interviews"])


async def _create_teams_meeting(interview_id: int) -> None:
    """Background task: create the Teams meeting for an interview and store its
    link/id on the row.

    Opens its own DB session. Resolves the organizer (interviewer M365 email) and
    the candidate as attendee. No-ops when the meeting provider is disabled or
    when a link already exists. Never propagates — a meeting failure must not
    affect the scheduled interview.
    """
    async with async_session_factory() as session:
        try:
            interview = await BaseRepository(session, Interview).get(interview_id)
            if interview is None or interview.teams_meeting_url:
                return
            interviewer = await BaseRepository(session, User).get(interview.interviewer_id)
            application = await BaseRepository(session, Application).get(
                interview.application_id
            )
            if interviewer is None or application is None:
                return
            candidate = await BaseRepository(session, Candidate).get(
                application.candidate_id
            )
            vacancy = await BaseRepository(session, Vacancy).get(application.vacancy_id)
            if candidate is None or vacancy is None:
                return
            candidate_user = await BaseRepository(session, User).get(candidate.user_id)
            vacancy_name = await ParameterRepository(session).get(vacancy.vacancy_name_id)

            subject_role = vacancy_name.name if vacancy_name else "Entrevista"
            subject = f"Entrevista: {candidate.first_name} {candidate.last_name} — {subject_role}"
            attendees = [
                e
                for e in (
                    candidate_user.email if candidate_user else None,
                    interview.extra_email,
                )
                if e
            ]

            provider = build_meeting_provider()
            result = await provider.create_meeting(
                MeetingRequest(
                    subject=subject,
                    start=interview.scheduled_at,
                    end=interview.ends_at,
                    organizer_email=interviewer.email,
                    attendee_emails=attendees,
                )
            )
            if result.success and result.join_url:
                interview.teams_meeting_url = result.join_url
                interview.teams_meeting_id = result.meeting_id
                await session.flush()
                await session.commit()
        except Exception:
            await session.rollback()
            return
    # The link is now persisted; send the invitation in a fresh session.
    await _send_interview_invitation(interview_id)


async def _send_interview_invitation(interview_id: int) -> None:
    """Background task: email the candidate the interview invitation with the
    Teams link.

    No-ops when the interview has no meeting link yet (nothing useful to send).
    Opens its own session. Never propagates — a failed email must not affect the
    scheduled interview.
    """
    async with async_session_factory() as session:
        try:
            interview = await BaseRepository(session, Interview).get(interview_id)
            if interview is None or not interview.teams_meeting_url:
                return
            application = await BaseRepository(session, Application).get(
                interview.application_id
            )
            if application is None:
                return
            candidate = await BaseRepository(session, Candidate).get(
                application.candidate_id
            )
            vacancy = await BaseRepository(session, Vacancy).get(application.vacancy_id)
            if candidate is None or vacancy is None:
                return
            candidate_user = await BaseRepository(session, User).get(candidate.user_id)
            if candidate_user is None:
                return
            vacancy_name = await ParameterRepository(session).get(vacancy.vacancy_name_id)
            rendered = render_interview_invitation_email(
                candidate.first_name,
                vacancy_name.name if vacancy_name else "la vacante",
                interview.scheduled_at,
                interview.teams_meeting_url,
            )
            recipients = [candidate_user.email]
            if interview.extra_email:
                recipients.append(interview.extra_email)
            dispatch = EmailDispatchService(session, build_email_sender())
            for to_email in recipients:
                await dispatch.send(
                    EmailMessage(
                        to_email=to_email,
                        subject=rendered.subject,
                        html_body=rendered.html_body,
                        text_body=rendered.text_body,
                    )
                )
            await session.commit()
        except Exception:
            await session.rollback()


async def _send_slot_offer_email(interview_id: int) -> None:
    """Background task: send the candidate the slot-selection email for Mode B.

    No-ops when the interview has no token or no offered_slots.
    Opens its own session. Never propagates.
    """
    async with async_session_factory() as session:
        try:
            interview = await BaseRepository(session, Interview).get(
                interview_id, include_inactive=True
            )
            if interview is None or not interview.offered_slots:
                return
            application = await BaseRepository(session, Application).get(
                interview.application_id
            )
            if application is None:
                return
            candidate = await BaseRepository(session, Candidate).get(
                application.candidate_id
            )
            vacancy = await BaseRepository(session, Vacancy).get(application.vacancy_id)
            if candidate is None or vacancy is None:
                return
            candidate_user = await BaseRepository(session, User).get(candidate.user_id)
            if candidate_user is None:
                return
            vacancy_name = await ParameterRepository(session).get(vacancy.vacancy_name_id)
            # The candidate chooses the slot from inside their account (login
            # required) — no public magic-link. The email is a second channel
            # that deep-links to "Mis postulaciones".
            choose_url = f"{settings.frontend_base_url}/candidato/mis-postulaciones"
            rendered = render_interview_slot_offer_email(
                candidate_first_name=candidate.first_name,
                vacancy_name=vacancy_name.name if vacancy_name else "la vacante",
                offered_slots=interview.offered_slots,
                choose_url=choose_url,
            )
            recipients = [candidate_user.email]
            if interview.extra_email:
                recipients.append(interview.extra_email)
            dispatch = EmailDispatchService(session, build_email_sender())
            for to_email in recipients:
                await dispatch.send(
                    EmailMessage(
                        to_email=to_email,
                        subject=rendered.subject,
                        html_body=rendered.html_body,
                        text_body=rendered.text_body,
                    )
                )
            await session.commit()
        except Exception:
            await session.rollback()


def get_service(session: SessionDep) -> InterviewService:
    return InterviewService(
        BaseRepository(session, Interview),
        BaseRepository(session, Application),
        BaseRepository(session, ProcessStage),
        BaseRepository(session, User),
        BaseRepository(session, Parameter),
    )


def get_slots_service(session: SessionDep) -> AvailableSlotsService:
    return AvailableSlotsService(
        availability_repo=BaseRepository(session, InterviewerAvailability),
        interview_repo=BaseRepository(session, Interview),
    )


ServiceDep = Annotated[InterviewService, Depends(get_service)]
SlotServiceDep = Annotated[AvailableSlotsService, Depends(get_slots_service)]
_READ = Depends(require_permission("recruitment.interviews.read"))


# ── Available slots (Mode B UI helpers) ───────────────────────────────────────


@router.get(
    "/available-slots",
    response_model=list[SlotRead],
    dependencies=[_READ],
)
async def get_available_slots(
    slots_service: SlotServiceDep,
    interviewer_id: Annotated[int, Query(description="User id of the interviewer")],
    target_date: Annotated[date, Query(description="Date (YYYY-MM-DD) to compute slots for")],
) -> list[SlotRead]:
    """Return free interview slots for an interviewer on a given date.

    Slots are computed from their active availability windows and filtered
    against existing (non-cancelled, active) interviews.
    """
    session = slots_service.availability_repo.session
    avail_rows_stmt = (
        select(InterviewerAvailability)
        .where(InterviewerAvailability.user_id == interviewer_id)
        .where(InterviewerAvailability.is_active.is_(True))
    )
    avail_rows = list((await session.execute(avail_rows_stmt)).scalars().all())

    # Map slot_duration by day so we can compute ends_at
    starts = await slots_service.get_slots(
        interviewer_id=interviewer_id,
        target_date=target_date,
    )

    # Compute end for each slot start using the window it falls in
    result: list[SlotRead] = []
    for slot_start in starts:
        # Find which window this slot belongs to (start_time <= slot <= end_time)
        slot_time = slot_start.replace(tzinfo=None)
        slot_t = slot_time.time()
        dur = 60  # fallback
        for row in avail_rows:
            if row.start_time <= slot_t < row.end_time:
                dur = row.slot_duration_min
                break
        slot_end = slot_start + timedelta(minutes=dur)
        result.append(SlotRead(start=slot_start, end=slot_end))

    return result


@router.get(
    "/interviewers",
    response_model=list[InterviewerRead],
    dependencies=[_READ],
)
async def get_interviewers(slots_service: SlotServiceDep) -> list[InterviewerRead]:
    """Return users that have at least one active availability window."""
    users = await slots_service.get_interviewers()
    return [InterviewerRead.model_validate(u) for u in users]


# ── Mode B — invite + in-account candidate endpoints ──────────────────────────


@router.post(
    "/invite",
    response_model=InterviewRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.interviews.create"))],
)
async def invite_interview(
    data: InterviewInviteCreate,
    service: ServiceDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> InterviewRead:
    """Mode B: create a pending interview and email the candidate offered slots.

    Creates the interview with status=offered, generates a one-time token,
    then sends the slot-offer email in the background.
    """
    try:
        created = await service.create_invite(data, current_user)
    except InterviewReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    background_tasks.add_task(_send_slot_offer_email, created.id)
    return InterviewRead.model_validate(created)


@router.get("/me/offers", response_model=list[InterviewRead])
async def get_my_offers(
    service: ServiceDep, current_user: CurrentUserDep
) -> list[InterviewRead]:
    """The authenticated candidate's own open interview offers (status=offered)."""
    offers = await service.list_offers_for_candidate(current_user.user_id)
    return [InterviewRead.model_validate(o) for o in offers]


@router.get("/me/scheduled", response_model=list[InterviewRead])
async def get_my_scheduled(
    service: ServiceDep, current_user: CurrentUserDep
) -> list[InterviewRead]:
    """The authenticated candidate's own scheduled (confirmed) interviews."""
    scheduled = await service.list_scheduled_for_candidate(current_user.user_id)
    return [InterviewRead.model_validate(s) for s in scheduled]


@router.post("/me/{interview_id}/confirm", response_model=InterviewRead)
async def confirm_my_offer(
    interview_id: int,
    data: SlotConfirmRequest,
    service: ServiceDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> InterviewRead:
    """The authenticated candidate confirms a chosen slot from their own offer.

    Ownership-scoped: 404 when the offer is missing or not theirs, 409 when it is
    expired or already confirmed, 400 when the slot is not among the offered ones,
    409 on a double-booking. On success, creates the Teams meeting and emails the
    invitation in the background.
    """
    try:
        confirmed = await service.confirm_slot_for_candidate(
            interview_id, current_user.user_id, data.chosen_slot
        )
    except InterviewNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InterviewOfferClosedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InterviewDoubleBookingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InterviewValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if settings.meetings_provider == "graph" and not confirmed.teams_meeting_url:
        background_tasks.add_task(_create_teams_meeting, confirmed.id)
    elif confirmed.teams_meeting_url:
        background_tasks.add_task(_send_interview_invitation, confirmed.id)
    return InterviewRead.model_validate(confirmed)


# ── Standard CRUD ─────────────────────────────────────────────────────────────


@router.get("", response_model=Page[InterviewRead], dependencies=[_READ])
async def list_interviews(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    application_id: Annotated[int | None, Query()] = None,
) -> Page[InterviewRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, application_id=application_id)
    return Page.create([InterviewRead.model_validate(i) for i in items], total, params)


@router.get("/{interview_id}", response_model=InterviewRead, dependencies=[_READ])
async def get_interview(interview_id: int, service: ServiceDep) -> InterviewRead:
    try:
        return InterviewRead.model_validate(await service.get(interview_id))
    except InterviewNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=InterviewRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.interviews.create"))],
)
async def create_interview(
    data: InterviewCreate,
    service: ServiceDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> InterviewRead:
    try:
        created = await service.create(data, current_user)
    except InterviewDoubleBookingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InterviewReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except InterviewValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # Create the Teams meeting out-of-band (when configured), then email the
    # candidate the invitation. If a link was supplied directly, just notify.
    if settings.meetings_provider == "graph" and not created.teams_meeting_url:
        background_tasks.add_task(_create_teams_meeting, created.id)
    elif created.teams_meeting_url:
        background_tasks.add_task(_send_interview_invitation, created.id)
    return InterviewRead.model_validate(created)


@router.patch(
    "/{interview_id}",
    response_model=InterviewRead,
    dependencies=[Depends(require_permission("recruitment.interviews.update"))],
)
async def update_interview(
    interview_id: int,
    data: InterviewUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> InterviewRead:
    try:
        updated = await service.update(interview_id, data, current_user)
    except InterviewNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InterviewReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except InterviewValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return InterviewRead.model_validate(updated)


@router.delete(
    "/{interview_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("recruitment.interviews.delete"))],
)
async def delete_interview(interview_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(interview_id)
    except InterviewNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
