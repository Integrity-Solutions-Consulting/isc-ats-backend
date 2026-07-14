"""Tests for Mode B: in-account candidate self-scheduling.

The candidate no longer chooses a slot through a public magic-link token. HR
offers slots (create_invite), which (1) emails the candidate AND (2) creates an
in-app notification (dual channel). The candidate then views and confirms the
offer from INSIDE their account — authenticated, ownership-scoped by
Interview -> Application -> Candidate.user_id == current_user.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.dependencies import CurrentUser
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.infrastructure.models import User
from app.modules.comms.infrastructure.models import Notification
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.interviews_schemas import (
    InterviewCreate,
    InterviewInviteCreate,
)
from app.modules.recruitment.application.interviews_service import (
    InterviewDoubleBookingError,
    InterviewNotFoundError,
    InterviewOfferClosedError,
    InterviewService,
    InterviewValidationError,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.interview_models import Interview
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")
ME_URL = "/api/v1/recruitment/interviews/me"


def _svc(session: AsyncSession) -> InterviewService:
    return InterviewService(
        BaseRepository(session, Interview),
        BaseRepository(session, Application),
        BaseRepository(session, ProcessStage),
        BaseRepository(session, User),
        BaseRepository(session, Parameter),
    )


async def _make_graph(
    session: AsyncSession,
) -> tuple[Application, ProcessStage, User, User, Parameter]:
    """Build the org graph + a DISTINCT interviewer user and candidate user.

    Returns (application, stage, interviewer_user, candidate_user, param). The
    candidate_user is the account that owns the application (ownership subject).
    """
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P", created_by=1)
    )
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:4]}", created_by=1)
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="A",
            last_name="B",
            email=f"{uuid.uuid4().hex[:8]}@co.test",
            created_by=1,
        )
    )
    dept = await BaseRepository(session, Department).add(
        Department(name=f"D{uuid.uuid4().hex[:4]}", created_by=1)
    )
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"Proc{uuid.uuid4().hex[:4]}",
            created_by=1,
        )
    )
    stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=param.id, order=1, created_by=1)
    )
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=param.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=param.id,
            city_id=param.id,
            work_mode_id=param.id,
            resource_level_id=param.id,
            status_id=param.id,
            created_by=1,
        )
    )
    staff_portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "staff"
    )
    cand_portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    interviewer = await BaseRepository(session, User).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@interviewer.local",
            portal_id=staff_portal.id,
            created_by=1,
        )
    )
    candidate_user = await BaseRepository(session, User).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@cand.local",
            portal_id=cand_portal.id,
            created_by=1,
        )
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(
            user_id=candidate_user.id, first_name="Ana", last_name="Gomez", created_by=1
        )
    )
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            created_by=1,
        )
    )
    return application, stage, interviewer, candidate_user, param


def _now() -> datetime:
    return datetime.now(UTC)


def _slots_payload(
    app_: Application, stage: ProcessStage, interviewer: User
) -> InterviewInviteCreate:
    base = _now() + timedelta(days=3)
    return InterviewInviteCreate(
        application_id=app_.id,
        process_stage_id=stage.id,
        interviewer_id=interviewer.id,
        offered_slots=[
            {"start": base.isoformat(), "end": (base + timedelta(hours=1)).isoformat()},
            {
                "start": (base + timedelta(hours=2)).isoformat(),
                "end": (base + timedelta(hours=3)).isoformat(),
            },
        ],
    )


# ── create_invite ─────────────────────────────────────────────────────────────


async def test_create_invite_sets_status_offered(session: AsyncSession) -> None:
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)

    offered = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "offered"
    )
    assert offered is not None
    assert invite.status_id == offered.id


async def test_create_invite_scheduled_at_is_null(session: AsyncSession) -> None:
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    assert invite.scheduled_at is None
    assert invite.ends_at is None


async def test_create_invite_stores_offered_slots(session: AsyncSession) -> None:
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    assert invite.offered_slots is not None
    assert len(invite.offered_slots) == 2


async def test_create_invite_sets_offer_expiry(session: AsyncSession) -> None:
    """The offer carries an expiry (~7 days) so it cannot be confirmed forever."""
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    assert invite.token_expires_at is not None
    delta = invite.token_expires_at - _now()
    assert timedelta(days=6) < delta < timedelta(days=8)


async def test_create_invite_creates_candidate_notification(session: AsyncSession) -> None:
    """Dual channel: create_invite must also create an in-app notification for the
    candidate's user, linked to the interview."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)

    notifs = (
        await session.execute(
            select(Notification).where(Notification.recipient_id == cand_user.id)
        )
    ).scalars().all()
    assert any(
        n.related_entity_type == "interview" and n.related_entity_id == invite.id
        for n in notifs
    )


# ── get_offer_for_candidate ───────────────────────────────────────────────────


async def test_get_offer_returns_own(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    found = await _svc(session).get_offer_for_candidate(invite.id, cand_user.id)
    assert found.id == invite.id


async def test_get_offer_rejects_other_user(session: AsyncSession) -> None:
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    with pytest.raises(InterviewNotFoundError):
        await _svc(session).get_offer_for_candidate(invite.id, 999999)


async def test_get_offer_expired_raises_closed(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    invite.token_expires_at = _now() - timedelta(hours=1)
    await session.flush()
    with pytest.raises(InterviewOfferClosedError):
        await _svc(session).get_offer_for_candidate(invite.id, cand_user.id)


# ── confirm_slot_for_candidate ────────────────────────────────────────────────


async def test_confirm_sets_scheduled_at(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    confirmed = await _svc(session).confirm_slot_for_candidate(
        invite.id, cand_user.id, invite.offered_slots[0]
    )
    assert confirmed.scheduled_at is not None
    assert confirmed.ends_at is not None


async def test_confirm_sets_status_scheduled_by_candidate(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    confirmed = await _svc(session).confirm_slot_for_candidate(
        invite.id, cand_user.id, invite.offered_slots[0]
    )
    repo = ParameterRepository(session)
    scheduled = await repo.get_by_type_and_code("interview_status", "scheduled")
    candidate_sched = await repo.get_by_type_and_code("interview_scheduler", "candidate")
    assert confirmed.status_id == scheduled.id
    assert confirmed.scheduled_by_id == candidate_sched.id


async def test_confirm_rejects_slot_not_in_offered(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    fake = {"start": "2020-01-01T00:00:00+00:00", "end": "2020-01-01T01:00:00+00:00"}
    with pytest.raises(InterviewValidationError):
        await _svc(session).confirm_slot_for_candidate(invite.id, cand_user.id, fake)


async def test_confirm_rejects_other_user(session: AsyncSession) -> None:
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    with pytest.raises(InterviewNotFoundError):
        await _svc(session).confirm_slot_for_candidate(
            invite.id, 999999, invite.offered_slots[0]
        )


async def test_confirm_one_time_use(session: AsyncSession) -> None:
    """After a successful confirm the offer is closed (already scheduled)."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    chosen = invite.offered_slots[0]
    await _svc(session).confirm_slot_for_candidate(invite.id, cand_user.id, chosen)

    with pytest.raises(InterviewOfferClosedError):
        await _svc(session).confirm_slot_for_candidate(invite.id, cand_user.id, chosen)


async def test_confirm_double_booking_guard(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    chosen = invite.offered_slots[0]
    start = datetime.fromisoformat(chosen["start"])
    end = datetime.fromisoformat(chosen["end"])
    await _svc(session).create(
        InterviewCreate(
            application_id=app_.id,
            process_stage_id=stage.id,
            interviewer_id=interviewer.id,
            scheduled_at=start,
            ends_at=end,
            status_id=param.id,
            scheduled_by_id=param.id,
        ),
        ACTOR,
    )
    with pytest.raises(InterviewDoubleBookingError):
        await _svc(session).confirm_slot_for_candidate(invite.id, cand_user.id, chosen)


# ── confirm_slot_for_candidate — HR notification (D4) ──────────────────────────


async def test_confirm_notifies_offer_creator(session: AsyncSession) -> None:
    """D4: confirming a slot creates an in-app SYSTEM notification for the
    offer's created_by (the HR user who created the invite), atomically with
    the status change."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    confirmed = await _svc(session).confirm_slot_for_candidate(
        invite.id, cand_user.id, invite.offered_slots[0]
    )

    notifs = (
        await session.execute(
            select(Notification).where(Notification.recipient_id == ACTOR.user_id)
        )
    ).scalars().all()
    matches = [
        n
        for n in notifs
        if n.related_entity_type == "interview" and n.related_entity_id == confirmed.id
    ]
    assert len(matches) == 1, "exactly one notification must be created for the offer creator"
    assert matches[0].title == "Un candidato eligió su horario"
    assert matches[0].created_by is None  # SYSTEM notification


async def test_confirm_notification_falls_back_to_interviewer_when_created_by_is_null(
    session: AsyncSession,
) -> None:
    """D4 fallback: legacy rows with created_by=None notify the interviewer instead."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    offered = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "offered"
    )
    hr_sched = await ParameterRepository(session).get_by_type_and_code(
        "interview_scheduler", "hr"
    )
    assert offered is not None
    assert hr_sched is not None
    base = _now() + timedelta(days=3)
    slot = {"start": base.isoformat(), "end": (base + timedelta(hours=1)).isoformat()}
    invite = await BaseRepository(session, Interview).add(
        Interview(
            application_id=app_.id,
            process_stage_id=stage.id,
            interviewer_id=interviewer.id,
            status_id=offered.id,
            scheduled_by_id=hr_sched.id,
            offered_slots=[slot],
            token_expires_at=_now() + timedelta(days=7),
            created_by=None,  # legacy row — no offer creator recorded
        )
    )
    confirmed = await _svc(session).confirm_slot_for_candidate(invite.id, cand_user.id, slot)

    notifs = (
        await session.execute(
            select(Notification).where(Notification.recipient_id == interviewer.id)
        )
    ).scalars().all()
    matches = [
        n
        for n in notifs
        if n.related_entity_type == "interview" and n.related_entity_id == confirmed.id
    ]
    assert len(matches) == 1
    assert matches[0].title == "Un candidato eligió su horario"


async def test_confirm_notification_dedups_when_created_by_equals_interviewer(
    session: AsyncSession,
) -> None:
    """D4 dedup: when the offer's creator IS the interviewer, confirm_slot_for_candidate
    must NOT add a second notification for that same recipient here — the route-layer
    background task (notify_slot_confirmed) already notifies the interviewer separately
    on every confirm, so adding one at the service level too would double it up."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    offered = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "offered"
    )
    hr_sched = await ParameterRepository(session).get_by_type_and_code(
        "interview_scheduler", "hr"
    )
    assert offered is not None
    assert hr_sched is not None
    base = _now() + timedelta(days=3)
    slot = {"start": base.isoformat(), "end": (base + timedelta(hours=1)).isoformat()}
    invite = await BaseRepository(session, Interview).add(
        Interview(
            application_id=app_.id,
            process_stage_id=stage.id,
            interviewer_id=interviewer.id,
            status_id=offered.id,
            scheduled_by_id=hr_sched.id,
            offered_slots=[slot],
            token_expires_at=_now() + timedelta(days=7),
            created_by=interviewer.id,  # creator IS the interviewer
        )
    )
    confirmed = await _svc(session).confirm_slot_for_candidate(invite.id, cand_user.id, slot)

    notifs = (
        await session.execute(
            select(Notification).where(Notification.recipient_id == interviewer.id)
        )
    ).scalars().all()
    matches = [
        n
        for n in notifs
        if n.related_entity_type == "interview" and n.related_entity_id == confirmed.id
    ]
    assert matches == []  # the service layer must not create a duplicate here


# ── list_offers_for_candidate ─────────────────────────────────────────────────


async def test_list_offers_returns_open_offers_for_candidate(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    offers = await _svc(session).list_offers_for_candidate(cand_user.id)
    assert any(o.id == invite.id for o in offers)


async def test_list_offers_excludes_other_candidates(session: AsyncSession) -> None:
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    offers = await _svc(session).list_offers_for_candidate(999999)
    assert offers == []


async def test_get_offer_rejects_cancelled_status(session: AsyncSession) -> None:
    """Bug 4: an HR-cancelled offer (status != offered) is no longer confirmable."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    cancelled = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "cancelled"
    )
    assert cancelled is not None
    invite.status_id = cancelled.id
    await session.flush()
    with pytest.raises(InterviewOfferClosedError):
        await _svc(session).get_offer_for_candidate(invite.id, cand_user.id)


async def test_confirm_rejects_cancelled_offer(session: AsyncSession) -> None:
    """Bug 4: a candidate cannot confirm a slot on a cancelled interview."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    cancelled = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "cancelled"
    )
    invite.status_id = cancelled.id
    await session.flush()
    with pytest.raises(InterviewOfferClosedError):
        await _svc(session).confirm_slot_for_candidate(
            invite.id, cand_user.id, invite.offered_slots[0]
        )


async def test_list_offers_excludes_expired(session: AsyncSession) -> None:
    """Bug 8: an offer past its token_expires_at must not surface as open."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    invite.token_expires_at = _now() - timedelta(hours=1)
    await session.flush()
    offers = await _svc(session).list_offers_for_candidate(cand_user.id)
    assert all(o.id != invite.id for o in offers)


async def test_double_booking_ignores_cancelled(session: AsyncSession) -> None:
    """Bug 6: a cancelled interview must NOT block a new booking on the same slot."""
    app_, stage, interviewer, cand_user, param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    chosen = invite.offered_slots[0]
    start = datetime.fromisoformat(chosen["start"])
    end = datetime.fromisoformat(chosen["end"])
    cancelled = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "cancelled"
    )
    # A CANCELLED interview overlapping the slot must not block confirmation.
    await _svc(session).create(
        InterviewCreate(
            application_id=app_.id,
            process_stage_id=stage.id,
            interviewer_id=interviewer.id,
            scheduled_at=start,
            ends_at=end,
            status_id=cancelled.id,
            scheduled_by_id=param.id,
        ),
        ACTOR,
    )
    confirmed = await _svc(session).confirm_slot_for_candidate(
        invite.id, cand_user.id, chosen
    )
    assert confirmed.scheduled_at is not None


async def test_confirm_rejects_naive_slot(session: AsyncSession) -> None:
    """Bug 10: a chosen_slot without a timezone offset is rejected."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    naive = {"start": "2999-01-01T10:00:00", "end": "2999-01-01T11:00:00"}
    with pytest.raises(InterviewValidationError):
        await _svc(session).confirm_slot_for_candidate(invite.id, cand_user.id, naive)


async def test_confirm_rejects_past_slot(session: AsyncSession) -> None:
    """Bug 10: a chosen_slot starting in the past is rejected."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    base = _now() - timedelta(days=1)
    past_slot = {
        "start": base.isoformat(),
        "end": (base + timedelta(hours=1)).isoformat(),
    }
    invite = await _svc(session).create_invite(
        InterviewInviteCreate(
            application_id=app_.id,
            process_stage_id=stage.id,
            interviewer_id=interviewer.id,
            offered_slots=[past_slot],
        ),
        ACTOR,
    )
    with pytest.raises(InterviewValidationError):
        await _svc(session).confirm_slot_for_candidate(
            invite.id, cand_user.id, past_slot
        )


# ── list_scheduled_for_candidate ──────────────────────────────────────────────


async def test_list_scheduled_returns_confirmed_for_candidate(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    await _svc(session).confirm_slot_for_candidate(
        invite.id, cand_user.id, invite.offered_slots[0]
    )
    scheduled = await _svc(session).list_scheduled_for_candidate(cand_user.id)
    assert any(i.id == invite.id for i in scheduled)


async def test_list_scheduled_excludes_open_offers(session: AsyncSession) -> None:
    """An un-confirmed offer is NOT yet scheduled, so it must not appear here."""
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    scheduled = await _svc(session).list_scheduled_for_candidate(cand_user.id)
    assert all(i.id != invite.id for i in scheduled)


async def test_list_scheduled_excludes_other_candidates(session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    await _svc(session).confirm_slot_for_candidate(
        invite.id, cand_user.id, invite.offered_slots[0]
    )
    scheduled = await _svc(session).list_scheduled_for_candidate(999999)
    assert scheduled == []


# ── HTTP (ownership) ──────────────────────────────────────────────────────────


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _bearer(user_id: int, portal: str = "candidate") -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def test_http_confirm_requires_auth(client: AsyncClient, session: AsyncSession) -> None:
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    response = await client.post(
        f"{ME_URL}/{invite.id}/confirm",
        json={"chosen_slot": invite.offered_slots[0]},
    )
    assert response.status_code in (401, 403)


async def test_http_confirm_own_offer(client: AsyncClient, session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    response = await client.post(
        f"{ME_URL}/{invite.id}/confirm",
        json={"chosen_slot": invite.offered_slots[0]},
        headers=_bearer(cand_user.id),
    )
    assert response.status_code == 200
    assert response.json()["scheduled_at"] is not None


async def test_http_confirm_still_succeeds_when_creator_is_interviewer(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D4 dedup does not break the confirm flow itself when creator == interviewer.

    (The full cross-session dedup between the service-level notification and the
    notify_slot_confirmed background task — which opens its own DB session — is
    covered at the service level by test_confirm_notification_dedups_when_created_by_equals_interviewer;
    the background task itself is explicitly unchanged per design D4.)
    """
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    offered = await ParameterRepository(session).get_by_type_and_code(
        "interview_status", "offered"
    )
    hr_sched = await ParameterRepository(session).get_by_type_and_code(
        "interview_scheduler", "hr"
    )
    assert offered is not None
    assert hr_sched is not None
    base = _now() + timedelta(days=3)
    slot = {"start": base.isoformat(), "end": (base + timedelta(hours=1)).isoformat()}
    invite = await BaseRepository(session, Interview).add(
        Interview(
            application_id=app_.id,
            process_stage_id=stage.id,
            interviewer_id=interviewer.id,
            status_id=offered.id,
            scheduled_by_id=hr_sched.id,
            offered_slots=[slot],
            token_expires_at=_now() + timedelta(days=7),
            created_by=interviewer.id,  # creator IS the interviewer
        )
    )

    response = await client.post(
        f"{ME_URL}/{invite.id}/confirm",
        json={"chosen_slot": slot},
        headers=_bearer(cand_user.id),
    )
    assert response.status_code == 200
    assert response.json()["scheduled_at"] is not None


async def test_http_confirm_other_user_404(client: AsyncClient, session: AsyncSession) -> None:
    app_, stage, interviewer, _cand, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    response = await client.post(
        f"{ME_URL}/{invite.id}/confirm",
        json={"chosen_slot": invite.offered_slots[0]},
        headers=_bearer(999999),
    )
    assert response.status_code == 404


async def test_http_list_my_offers(client: AsyncClient, session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    response = await client.get(f"{ME_URL}/offers", headers=_bearer(cand_user.id))
    assert response.status_code == 200
    assert any(item["id"] == invite.id for item in response.json())


async def test_http_list_my_scheduled(client: AsyncClient, session: AsyncSession) -> None:
    app_, stage, interviewer, cand_user, _param = await _make_graph(session)
    invite = await _svc(session).create_invite(_slots_payload(app_, stage, interviewer), ACTOR)
    await _svc(session).confirm_slot_for_candidate(
        invite.id, cand_user.id, invite.offered_slots[0]
    )
    response = await client.get(f"{ME_URL}/scheduled", headers=_bearer(cand_user.id))
    assert response.status_code == 200
    assert any(item["id"] == invite.id for item in response.json())
