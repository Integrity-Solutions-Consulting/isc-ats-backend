"""GET /interviews/available-slots must report each slot's real `end` using the
interviewer's configured `slot_duration_min` — not a hardcoded 60 minutes.

Bug: the route matched each UTC slot_start against LOCAL (Ecuador) window
start_time/end_time without converting back to local time first. For any
window not spanning ~19:00-24:00 local, the +5h UTC shift pushes the slot's
raw clock digits outside the window's local numeric range, silently falling
back to `dur = 60` regardless of the interviewer's real configuration.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import time

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.interview_models import InterviewerAvailability
from app.shared.repository import BaseRepository

# 2026-06-15 = Monday (weekday 0)
_MONDAY = "2026-06-15"
_WEEKDAY = 0


async def _make_interviewer(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    return await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@slots-route.local", portal_id=portal.id)
    )


async def test_available_slots_route_reports_configured_duration_not_hardcoded_60(
    session: AsyncSession,
) -> None:
    """Interviewer configured for 30-minute slots, 08:00-09:00 local (Monday).

    08:00 local -> 13:00 UTC, which is OUTSIDE the local numeric range
    08:00-09:00 — exactly the case that silently fell back to 60 minutes."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    admin_result = await bootstrap_admin(session, f"admin-{tag}@test.local", "S3cret")
    interviewer = await _make_interviewer(session)
    await BaseRepository(session, InterviewerAvailability).add(
        InterviewerAvailability(
            user_id=interviewer.id,
            day_of_week=_WEEKDAY,
            start_time=time(8, 0),
            end_time=time(9, 0),
            slot_duration_min=30,
            buffer_min=0,
        )
    )

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(admin_result.user_id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get(
                "/api/v1/recruitment/interviews/available-slots",
                params={"interviewer_id": interviewer.id, "target_date": _MONDAY},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert len(body) == 2, f"Expected 2 slots (08:00, 08:30 local), got {body}"
        for slot in body:
            from datetime import datetime  # noqa: PLC0415

            start = datetime.fromisoformat(slot["start"])
            end = datetime.fromisoformat(slot["end"])
            duration_min = (end - start).total_seconds() / 60
            assert duration_min == 30, (
                f"Expected 30-minute slot, got {duration_min} minutes: {slot}"
            )
    finally:
        app.dependency_overrides.clear()
