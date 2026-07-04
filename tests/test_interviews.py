import uuid
from datetime import UTC, datetime, time, timedelta

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
from app.modules.recruitment.api.interviewer_availability_schemas import (
    AvailabilityCreate,
)
from app.modules.recruitment.api.interviews_schemas import InterviewCreate
from app.modules.recruitment.application.interviewer_availability_service import (
    AvailabilityReferenceError,
    AvailabilityValidationError,
    InterviewerAvailabilityService,
)
from app.modules.recruitment.application.interviews_service import (
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
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _interview_service(session: AsyncSession) -> InterviewService:
    return InterviewService(
        BaseRepository(session, Interview),
        BaseRepository(session, Application),
        BaseRepository(session, ProcessStage),
        BaseRepository(session, User),
        BaseRepository(session, Parameter),
    )


def _availability_service(session: AsyncSession) -> InterviewerAvailabilityService:
    return InterviewerAvailabilityService(
        BaseRepository(session, InterviewerAvailability),
        BaseRepository(session, User),
    )


async def _make_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    return await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )


async def _make_interview_graph(
    session: AsyncSession,
) -> tuple[Application, ProcessStage, User, Parameter]:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="ACME"))
    contact = await BaseRepository(session, Contact).add(
        Contact(client_company_id=company.id, first_name="A", last_name="B", email="a@b.co")
    )
    dept = await BaseRepository(session, Department).add(Department(name="Tech"))
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(process_id=process.id, stage_id=param.id, order=1)
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
        )
    )
    user = await _make_user(session)
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="Juan", last_name="Perez")
    )
    application = await BaseRepository(session, Application).add(
        Application(vacancy_id=vacancy.id, candidate_id=candidate.id, status_id=param.id)
    )
    interviewer = await _make_user(session)
    return application, stage, interviewer, param


def _interview_payload(application, stage, interviewer, param) -> InterviewCreate:
    start = datetime.now(UTC)
    return InterviewCreate(
        application_id=application.id,
        process_stage_id=stage.id,
        interviewer_id=interviewer.id,
        scheduled_at=start,
        ends_at=start + timedelta(hours=1),
        status_id=param.id,
        scheduled_by_id=param.id,
    )


async def test_create_interview_succeeds(session: AsyncSession) -> None:
    app_, stage, interviewer, param = await _make_interview_graph(session)
    interview = await _interview_service(session).create(
        _interview_payload(app_, stage, interviewer, param), ACTOR
    )
    assert interview.id is not None
    assert interview.created_by == ACTOR.user_id


async def test_create_interview_rejects_inverted_window(session: AsyncSession) -> None:
    app_, stage, interviewer, param = await _make_interview_graph(session)
    payload = _interview_payload(app_, stage, interviewer, param)
    payload.ends_at = payload.scheduled_at  # not after start

    with pytest.raises(InterviewValidationError):
        await _interview_service(session).create(payload, ACTOR)


async def test_create_interview_rejects_unknown_application(session: AsyncSession) -> None:
    app_, stage, interviewer, param = await _make_interview_graph(session)
    payload = _interview_payload(app_, stage, interviewer, param)
    payload.application_id = 999999

    with pytest.raises(InterviewReferenceError):
        await _interview_service(session).create(payload, ACTOR)


async def test_create_availability_succeeds(session: AsyncSession) -> None:
    user = await _make_user(session)
    availability = await _availability_service(session).create(
        AvailabilityCreate(
            user_id=user.id, day_of_week=1, start_time=time(9, 0), end_time=time(17, 0)
        ),
        ACTOR,
    )
    assert availability.id is not None
    assert availability.slot_duration_min == 60


async def test_create_availability_persists_buffer_min(session: AsyncSession) -> None:
    user = await _make_user(session)
    svc = _availability_service(session)
    with_buffer = await svc.create(
        AvailabilityCreate(
            user_id=user.id,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
            buffer_min=10,
        ),
        ACTOR,
    )
    assert with_buffer.buffer_min == 10

    default_buffer = await svc.create(
        AvailabilityCreate(
            user_id=user.id, day_of_week=2, start_time=time(9, 0), end_time=time(17, 0)
        ),
        ACTOR,
    )
    assert default_buffer.buffer_min == 0


async def test_create_availability_rejects_inverted_window(session: AsyncSession) -> None:
    user = await _make_user(session)
    with pytest.raises(AvailabilityValidationError):
        await _availability_service(session).create(
            AvailabilityCreate(
                user_id=user.id, day_of_week=1, start_time=time(17, 0), end_time=time(9, 0)
            ),
            ACTOR,
        )


async def test_create_availability_rejects_unknown_user(session: AsyncSession) -> None:
    with pytest.raises(AvailabilityReferenceError):
        await _availability_service(session).create(
            AvailabilityCreate(
                user_id=999999, day_of_week=1, start_time=time(9, 0), end_time=time(17, 0)
            ),
            ACTOR,
        )


async def test_update_interview_rejects_double_booking(session: AsyncSession) -> None:
    from app.modules.recruitment.api.interviews_schemas import InterviewUpdate
    from app.modules.recruitment.application.interviews_service import InterviewDoubleBookingError

    app_, stage, interviewer, param = await _make_interview_graph(session)
    svc = _interview_service(session)
    
    # 1. Create first interview
    start1 = datetime.now(UTC) + timedelta(days=1)
    payload1 = _interview_payload(app_, stage, interviewer, param)
    payload1.scheduled_at = start1
    payload1.ends_at = start1 + timedelta(hours=1)
    i1 = await svc.create(payload1, ACTOR)
    
    # 2. Create second interview at a different time
    start2 = start1 + timedelta(hours=3)
    payload2 = _interview_payload(app_, stage, interviewer, param)
    payload2.scheduled_at = start2
    payload2.ends_at = start2 + timedelta(hours=1)
    i2 = await svc.create(payload2, ACTOR)
    
    # 3. Try to update second interview to overlap with the first one -> should raise InterviewDoubleBookingError
    update_payload = InterviewUpdate(
        scheduled_at=start1 + timedelta(minutes=30),  # Overlaps [start1, start1 + 1h)
        ends_at=start1 + timedelta(hours=1, minutes=30),
    )
    with pytest.raises(InterviewDoubleBookingError):
        await svc.update(i2.id, update_payload, ACTOR)
