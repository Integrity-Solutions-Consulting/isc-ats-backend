"""Tests for Task 1.10: Mode A double-booking guard.

When creating an interview (Mode A), the system must reject a second booking
for the same interviewer in an overlapping time window (409 Conflict).
Cancelled and soft-deleted interviews must NOT block the slot.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.interviews_schemas import InterviewCreate
from app.modules.recruitment.application.interviews_service import (
    InterviewDoubleBookingError,
    InterviewService,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.interview_models import Interview
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


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
) -> tuple[Application, ProcessStage, User, Parameter]:
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
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    user = await BaseRepository(session, User).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@guard.local",
            portal_id=portal.id,
            created_by=1,
        )
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="Juan", last_name="Perez", created_by=1)
    )
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id, candidate_id=candidate.id, status_id=param.id, created_by=1
        )
    )
    return application, stage, user, param


def _payload(app_: Application, stage: ProcessStage, interviewer: User, param: Parameter,
             start: datetime, end: datetime) -> InterviewCreate:
    return InterviewCreate(
        application_id=app_.id,
        process_stage_id=stage.id,
        interviewer_id=interviewer.id,
        scheduled_at=start,
        ends_at=end,
        status_id=param.id,
        scheduled_by_id=param.id,
    )


async def test_double_booking_same_window_raises_409(session: AsyncSession) -> None:
    """Two overlapping interviews for the same interviewer must raise InterviewDoubleBookingError.
    """
    app_, stage, interviewer, param = await _make_graph(session)
    start = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    end = datetime(2026, 6, 20, 11, 0, tzinfo=UTC)

    # First booking succeeds
    await _svc(session).create(_payload(app_, stage, interviewer, param, start, end), ACTOR)

    # Second booking with identical window raises
    with pytest.raises(InterviewDoubleBookingError):
        await _svc(session).create(_payload(app_, stage, interviewer, param, start, end), ACTOR)


async def test_double_booking_partial_overlap_raises_409(session: AsyncSession) -> None:
    """Even a partial overlap (e.g., second starts mid-first) raises."""
    app_, stage, interviewer, param = await _make_graph(session)
    start1 = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    end1 = datetime(2026, 6, 20, 11, 0, tzinfo=UTC)
    start2 = datetime(2026, 6, 20, 10, 30, tzinfo=UTC)
    end2 = datetime(2026, 6, 20, 11, 30, tzinfo=UTC)

    await _svc(session).create(_payload(app_, stage, interviewer, param, start1, end1), ACTOR)

    with pytest.raises(InterviewDoubleBookingError):
        await _svc(session).create(_payload(app_, stage, interviewer, param, start2, end2), ACTOR)


async def test_non_overlapping_window_succeeds(session: AsyncSession) -> None:
    """Adjacent (non-overlapping) windows for same interviewer must succeed."""
    app_, stage, interviewer, param = await _make_graph(session)
    start1 = datetime(2026, 6, 20, 9, 0, tzinfo=UTC)
    end1 = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    start2 = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    end2 = datetime(2026, 6, 20, 11, 0, tzinfo=UTC)

    await _svc(session).create(_payload(app_, stage, interviewer, param, start1, end1), ACTOR)
    interview2 = await _svc(session).create(
        _payload(app_, stage, interviewer, param, start2, end2), ACTOR
    )
    assert interview2.id is not None


async def test_cancelled_interview_does_not_block(session: AsyncSession) -> None:
    """A soft-deleted (is_active=False) interview must not block the same slot."""
    app_, stage, interviewer, param = await _make_graph(session)
    start = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    end = datetime(2026, 6, 20, 11, 0, tzinfo=UTC)

    # Create and then soft-delete the first interview
    first = await _svc(session).create(
        _payload(app_, stage, interviewer, param, start, end), ACTOR
    )
    first.is_active = False
    await session.flush()

    # Same slot should now be bookable
    second = await _svc(session).create(
        _payload(app_, stage, interviewer, param, start, end), ACTOR
    )
    assert second.id is not None
