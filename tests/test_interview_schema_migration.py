"""Tests for Task 1.3: schema changes to interviewer_availability and interviews.

Verifies:
- InterviewerAvailability.buffer_min exists with default 0
- Interview.token_expires_at nullable datetime column
- Interview.scheduled_at and ends_at are nullable
"""

from datetime import UTC, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

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
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.interview_models import (
    Interview,
    InterviewerAvailability,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository


async def _make_user(session: AsyncSession) -> User:
    import uuid
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    return await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@schema.local", portal_id=portal.id)
    )


async def test_interviewer_availability_buffer_min_defaults_to_zero(
    session: AsyncSession,
) -> None:
    """InterviewerAvailability.buffer_min must default to 0 if not specified."""
    user = await _make_user(session)
    avail = await BaseRepository(session, InterviewerAvailability).add(
        InterviewerAvailability(
            user_id=user.id,
            day_of_week=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
            created_by=1,
            ip_created="127.0.0.1",
        )
    )
    assert avail.buffer_min == 0


async def test_interviewer_availability_buffer_min_can_be_set(
    session: AsyncSession,
) -> None:
    """InterviewerAvailability.buffer_min accepts explicit values."""
    user = await _make_user(session)
    avail = await BaseRepository(session, InterviewerAvailability).add(
        InterviewerAvailability(
            user_id=user.id,
            day_of_week=2,
            start_time=time(10, 0),
            end_time=time(18, 0),
            buffer_min=10,
            created_by=1,
            ip_created="127.0.0.1",
        )
    )
    assert avail.buffer_min == 10


async def _make_interview_for_schema_test(session: AsyncSession) -> tuple:
    """Build the minimal graph needed to insert an Interview row."""
    import uuid

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
            email=f"{uuid.uuid4().hex[:12]}@schema2.local",
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


async def test_interview_scheduled_at_nullable(session: AsyncSession) -> None:
    """Interview.scheduled_at must accept NULL (for Mode B invite flow)."""
    application, stage, user, param = await _make_interview_for_schema_test(session)
    interview = await BaseRepository(session, Interview).add(
        Interview(
            application_id=application.id,
            process_stage_id=stage.id,
            interviewer_id=user.id,
            scheduled_at=None,
            ends_at=None,
            status_id=param.id,
            scheduled_by_id=param.id,
            created_by=1,
            ip_created="127.0.0.1",
        )
    )
    assert interview.id is not None
    assert interview.scheduled_at is None
    assert interview.ends_at is None


async def test_interview_token_expires_at_nullable(session: AsyncSession) -> None:
    """Interview.token_expires_at must exist and accept NULL."""
    application, stage, user, param = await _make_interview_for_schema_test(session)
    interview = await BaseRepository(session, Interview).add(
        Interview(
            application_id=application.id,
            process_stage_id=stage.id,
            interviewer_id=user.id,
            scheduled_at=None,
            ends_at=None,
            status_id=param.id,
            scheduled_by_id=param.id,
            token_expires_at=None,
            created_by=1,
            ip_created="127.0.0.1",
        )
    )
    assert interview.token_expires_at is None


async def test_interview_token_expires_at_stores_datetime(session: AsyncSession) -> None:
    """Interview.token_expires_at stores a tz-aware datetime correctly."""
    application, stage, user, param = await _make_interview_for_schema_test(session)
    expires = datetime.now(UTC) + timedelta(days=7)
    interview = await BaseRepository(session, Interview).add(
        Interview(
            application_id=application.id,
            process_stage_id=stage.id,
            interviewer_id=user.id,
            scheduled_at=None,
            ends_at=None,
            status_id=param.id,
            scheduled_by_id=param.id,
            token_expires_at=expires,
            created_by=1,
            ip_created="127.0.0.1",
        )
    )
    assert interview.token_expires_at is not None
    # DB round-trip: microseconds may differ slightly, compare to the second
    delta = abs((interview.token_expires_at - expires).total_seconds())
    assert delta < 2
