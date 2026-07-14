"""Tests for GET /interviews/available-slots and GET /interviews/interviewers.

Task 1.6: available-slots tests (Task 1.7 is implementation)
Task 1.8: interviewers tests (Task 1.9 is implementation)

Both are integration tests against the service layer (not HTTP).
"""

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.application.available_slots_service import (
    AvailableSlotsService,
)
from app.modules.recruitment.infrastructure.interview_models import (
    Interview,
    InterviewerAvailability,
)
from app.shared.repository import BaseRepository


async def _make_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    return await BaseRepository(session, User).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@slots.local",
            portal_id=portal.id,
            created_by=1,
            ip_created="127.0.0.1",
        )
    )


async def _make_availability(
    session: AsyncSession,
    user_id: int,
    *,
    day_of_week: int = 0,
    start: time = time(9, 0),
    end: time = time(11, 0),
    slot_duration_min: int = 60,
    buffer_min: int = 0,
    is_active: bool = True,
) -> InterviewerAvailability:
    avail = InterviewerAvailability(
        user_id=user_id,
        day_of_week=day_of_week,
        start_time=start,
        end_time=end,
        slot_duration_min=slot_duration_min,
        buffer_min=buffer_min,
        created_by=1,
        ip_created="127.0.0.1",
    )
    if not is_active:
        avail.is_active = False
    return await BaseRepository(session, InterviewerAvailability).add(avail)


def _make_svc(session: AsyncSession) -> AvailableSlotsService:
    return AvailableSlotsService(
        availability_repo=BaseRepository(session, InterviewerAvailability),
        interview_repo=BaseRepository(session, Interview),
    )


# ── available-slots tests ─────────────────────────────────────────────────────


# 2026-06-15 = Monday (weekday 0)
_MONDAY = date(2026, 6, 15)


async def test_available_slots_basic(session: AsyncSession) -> None:
    """Returns slots from active availability for the correct weekday.

    Availability is configured in Ecuador local time (R1); 09:00/10:00 local
    must come back as 14:00/15:00 UTC (local + 5h), never the raw local hour.
    """
    user = await _make_user(session)
    await _make_availability(session, user.id, day_of_week=0, start=time(9, 0), end=time(11, 0))

    svc = _make_svc(session)
    slots = await svc.get_slots(
        interviewer_id=user.id,
        target_date=_MONDAY,
    )
    times = [(s.hour, s.minute) for s in slots]
    assert times == [(14, 0), (15, 0)]


async def test_available_slots_inactive_excluded(session: AsyncSession) -> None:
    """Inactive availability rows produce no slots."""
    user = await _make_user(session)
    avail = await _make_availability(
        session, user.id, day_of_week=0, start=time(9, 0), end=time(11, 0)
    )
    # Soft-delete it
    avail.is_active = False
    await session.flush()

    svc = _make_svc(session)
    slots = await svc.get_slots(interviewer_id=user.id, target_date=_MONDAY)
    assert slots == []


async def test_available_slots_wrong_day_excluded(session: AsyncSession) -> None:
    """Availability for a different weekday is not returned for the target date."""
    user = await _make_user(session)
    # Tuesday = 1, but target is Monday = 0
    await _make_availability(session, user.id, day_of_week=1, start=time(9, 0), end=time(11, 0))

    svc = _make_svc(session)
    slots = await svc.get_slots(interviewer_id=user.id, target_date=_MONDAY)
    assert slots == []


async def test_available_slots_with_buffer(session: AsyncSession) -> None:
    """Buffer reduces the number of slots (30-min slot + 10-min buffer).

    10:00/10:40/11:20 local -> 15:00/15:40/16:20 UTC.
    """
    user = await _make_user(session)
    await _make_availability(
        session,
        user.id,
        day_of_week=0,
        start=time(10, 0),
        end=time(12, 0),
        slot_duration_min=30,
        buffer_min=10,
    )
    svc = _make_svc(session)
    slots = await svc.get_slots(interviewer_id=user.id, target_date=_MONDAY)
    times = [(s.hour, s.minute) for s in slots]
    assert times == [(15, 0), (15, 40), (16, 20)]


# ── R1: day-boundary widening (local day may span two UTC calendar days) ──────


async def test_available_slots_late_window_booking_near_local_midnight_blocks_slot(
    session: AsyncSession,
) -> None:
    """A late local window's booked interval can fall on the NEXT UTC calendar day.

    Availability 21:00-23:00 Ecuador local (30-min slots, no buffer) ->
    slots at 21:00, 21:30, 22:00, 22:30 local == 02:00, 02:30, 03:00, 03:30 UTC
    on the NEXT day (2026-06-16).

    A booked interview at 22:15-22:45 local (== 2026-06-16 03:15-03:45 UTC —
    a UTC calendar day AFTER target_date) must still be fetched and block the
    overlapping 22:00 and 22:30 local slots. Before the fix, the DB query
    bounded booked intervals to the UTC calendar day of target_date and would
    have missed this interview entirely, leaking a double-booked slot.
    """
    user = await _make_user(session)
    await _make_availability(
        session,
        user.id,
        day_of_week=0,
        start=time(21, 0),
        end=time(23, 0),
        slot_duration_min=30,
        buffer_min=0,
    )

    from app.modules.org.infrastructure.models import (
        ClientCompany,
        Contact,
        Department,
        Parameter,
        Process,
        ProcessStage,
    )
    from app.modules.recruitment.infrastructure.application_models import Application
    from app.modules.recruitment.infrastructure.candidate_models import Candidate
    from app.modules.recruitment.infrastructure.models import Vacancy

    param_repo = ParameterRepository(session)
    dummy_param = await BaseRepository(session, Parameter).add(
        Parameter(type="x_slots_boundary_test", code=uuid.uuid4().hex[:8], name="P", created_by=1)
    )
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:4]}", created_by=1)
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="A",
            last_name="B",
            email=f"{uuid.uuid4().hex[:8]}@boundary.test",
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
        ProcessStage(process_id=process.id, stage_id=dummy_param.id, order=1, created_by=1)
    )
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=dummy_param.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=dummy_param.id,
            city_id=dummy_param.id,
            work_mode_id=dummy_param.id,
            resource_level_id=dummy_param.id,
            status_id=dummy_param.id,
            created_by=1,
        )
    )
    portal = await param_repo.get_by_type_and_code("user_portal", "staff")
    candidate_user = await BaseRepository(session, User).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@boundary-candidate.local",
            portal_id=portal.id,
            created_by=1,
            ip_created="127.0.0.1",
        )
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=candidate_user.id, first_name="X", last_name="Y", created_by=1)
    )
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=dummy_param.id,
            created_by=1,
        )
    )
    scheduled_param = await param_repo.get_by_type_and_code("interview_status", "scheduled")
    assert scheduled_param is not None

    booked_start = datetime(2026, 6, 16, 3, 15, tzinfo=UTC)
    booked_end = datetime(2026, 6, 16, 3, 45, tzinfo=UTC)
    await BaseRepository(session, Interview).add(
        Interview(
            application_id=application.id,
            process_stage_id=stage.id,
            interviewer_id=user.id,
            scheduled_at=booked_start,
            ends_at=booked_end,
            status_id=scheduled_param.id,
            scheduled_by_id=dummy_param.id,
            created_by=1,
            ip_created="127.0.0.1",
        )
    )

    svc = _make_svc(session)
    slots = await svc.get_slots(interviewer_id=user.id, target_date=_MONDAY)
    times = [(s.hour, s.minute) for s in slots]
    # 22:00 (02:00-03:00 -> ends_at 03:00Z... wait compare in UTC) and 22:30 local
    # (03:00Z, 03:30Z) overlap the booked 03:15-03:45Z interval and must be excluded.
    assert (3, 0) not in times, "22:00 local slot must be blocked by the near-midnight booking"
    assert (3, 30) not in times, "22:30 local slot must be blocked by the near-midnight booking"
    # 21:00 and 21:30 local (02:00Z, 02:30Z) do not overlap and must remain free.
    assert (2, 0) in times
    assert (2, 30) in times


# ── interviewers tests ────────────────────────────────────────────────────────


async def test_get_interviewers_returns_users_with_active_availability(
    session: AsyncSession,
) -> None:
    """Users with active availability rows are returned."""
    user = await _make_user(session)
    await _make_availability(session, user.id, day_of_week=1)

    svc = _make_svc(session)
    interviewers = await svc.get_interviewers()
    ids = [u.id for u in interviewers]
    assert user.id in ids


async def test_get_interviewers_excludes_users_without_availability(
    session: AsyncSession,
) -> None:
    """Users without any active availability row must not appear."""
    user = await _make_user(session)
    # No availability row added

    svc = _make_svc(session)
    interviewers = await svc.get_interviewers()
    ids = [u.id for u in interviewers]
    assert user.id not in ids


async def test_get_interviewers_excludes_inactive_availability(
    session: AsyncSession,
) -> None:
    """A user with only inactive availability must not appear."""
    user = await _make_user(session)
    avail = await _make_availability(session, user.id, day_of_week=0)
    avail.is_active = False
    await session.flush()

    svc = _make_svc(session)
    interviewers = await svc.get_interviewers()
    ids = [u.id for u in interviewers]
    assert user.id not in ids


async def test_get_interviewers_no_duplicate_users(session: AsyncSession) -> None:
    """A user with multiple availability windows appears only once."""
    user = await _make_user(session)
    await _make_availability(session, user.id, day_of_week=0)
    await _make_availability(session, user.id, day_of_week=1)

    svc = _make_svc(session)
    interviewers = await svc.get_interviewers()
    ids = [u.id for u in interviewers]
    assert ids.count(user.id) == 1


# ── REQ-04: cancelled interviews must not block slots ─────────────────────────


async def test_cancelled_interview_does_not_block_slot(session: AsyncSession) -> None:
    """REQ-04: an is_active=True interview with status=cancelled must NOT block the slot.

    The service must exclude cancelled interviews when computing free slots so
    that the slot is still returned as available.
    """
    from app.modules.org.infrastructure.models import (
        ClientCompany,
        Contact,
        Department,
        Parameter,
        Process,
        ProcessStage,
    )
    from app.modules.recruitment.infrastructure.application_models import Application
    from app.modules.recruitment.infrastructure.candidate_models import Candidate
    from app.modules.recruitment.infrastructure.models import Vacancy

    # Resolve the 'cancelled' interview_status parameter
    param_repo = ParameterRepository(session)
    cancelled_param = await param_repo.get_by_type_and_code("interview_status", "cancelled")
    assert cancelled_param is not None, (
        "Parameter (interview_status, cancelled) not found — run seed migration"
    )

    # Build the minimum graph required for an Interview row
    dummy_param = await BaseRepository(session, Parameter).add(
        Parameter(type="x_slots_test", code=uuid.uuid4().hex[:8], name="P", created_by=1)
    )
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:4]}", created_by=1)
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="A",
            last_name="B",
            email=f"{uuid.uuid4().hex[:8]}@slots.test",
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
        ProcessStage(process_id=process.id, stage_id=dummy_param.id, order=1, created_by=1)
    )
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=dummy_param.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=dummy_param.id,
            city_id=dummy_param.id,
            work_mode_id=dummy_param.id,
            resource_level_id=dummy_param.id,
            status_id=dummy_param.id,
            created_by=1,
        )
    )
    portal = await param_repo.get_by_type_and_code("user_portal", "staff")
    interviewer = await BaseRepository(session, User).add(
        User(
            email=f"{uuid.uuid4().hex[:12]}@cancelled.local",
            portal_id=portal.id,
            created_by=1,
            ip_created="127.0.0.1",
        )
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=interviewer.id, first_name="X", last_name="Y", created_by=1)
    )
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=dummy_param.id,
            created_by=1,
        )
    )

    # Monday 2026-06-15, availability 09:00–11:00 → slots at 09:00 and 10:00
    await _make_availability(
        session, interviewer.id, day_of_week=0, start=time(9, 0), end=time(11, 0)
    )

    # Cancelled interview overlapping the 09:00 slot (is_active=True, NOT soft-deleted)
    slot_start = datetime(_MONDAY.year, _MONDAY.month, _MONDAY.day, 9, 0, tzinfo=UTC)
    slot_end = slot_start.replace(hour=10)
    cancelled_interview = Interview(
        application_id=application.id,
        process_stage_id=stage.id,
        interviewer_id=interviewer.id,
        scheduled_at=slot_start,
        ends_at=slot_end,
        status_id=cancelled_param.id,
        scheduled_by_id=dummy_param.id,
        created_by=1,
        ip_created="127.0.0.1",
    )
    # is_active=True by default — we do NOT soft-delete it
    await BaseRepository(session, Interview).add(cancelled_interview)

    svc = _make_svc(session)
    slots = await svc.get_slots(interviewer_id=interviewer.id, target_date=_MONDAY)
    times = [(s.hour, s.minute) for s in slots]
    # Both slots must be available; cancelled interview must NOT block 09:00 local
    # (== 14:00 UTC) / 10:00 local (== 15:00 UTC).
    assert (14, 0) in times, "09:00 local slot must be available despite cancelled interview"
    assert (15, 0) in times
