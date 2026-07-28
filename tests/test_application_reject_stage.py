"""Rejecting an application records the stage the candidate had reached, and
requires a free-text rejection_reason.

When a recruiter rejects (sets current_stage_id to None), the prior stage is
captured in rejected_at_stage_id so the candidate UI can show how far they got
before being rejected, instead of an all-empty stepper. A manual reject must
always carry a reason — HR writes it in the Kanban move — so the candidate
always receives a concrete explanation (email + in-app notification).
"""

import uuid

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
from app.modules.recruitment.api.applications_schemas import ApplicationUpdate
from app.modules.recruitment.application.applications_service import (
    ApplicationService,
    RejectionReasonRequiredError,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.applications_repository import (
    ApplicationRepository,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository


def _service(session: AsyncSession) -> ApplicationService:
    return ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        ParameterRepository(session),
    )


async def _graph(session: AsyncSession) -> tuple[Parameter, Vacancy, ProcessStage, Candidate]:
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="Co"))
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="C",
            last_name="D",
            email=f"{uuid.uuid4().hex[:8]}@d.co",
        )
    )
    dept = await BaseRepository(session, Department).add(Department(name="Eng"))
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
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="J", last_name="P")
    )
    return param, vacancy, stage, candidate


async def test_reject_records_stage_reached(session: AsyncSession) -> None:
    param, vacancy, stage, candidate = await _graph(session)
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=stage.id,
        )
    )
    await session.flush()

    actor = CurrentUser(user_id=1, ip=None)
    updated = await _service(session).update(
        application.id,
        ApplicationUpdate(current_stage_id=None, rejection_reason="No cumple el perfil técnico."),
        actor,
    )

    rejected = await ParameterRepository(session).get_by_type_and_code(
        "application_status", "rejected"
    )
    assert rejected is not None, "application_status:rejected must be seeded"
    assert updated.current_stage_id is None
    assert updated.status_id == rejected.id
    assert updated.rejected_at_stage_id == stage.id
    assert updated.rejection_reason == "No cumple el perfil técnico."


async def test_reject_without_prior_stage_records_none(session: AsyncSession) -> None:
    # Defensive: rejecting an application that had no stage leaves the field None.
    param, vacancy, _stage, candidate = await _graph(session)
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=None,
        )
    )
    await session.flush()

    actor = CurrentUser(user_id=1, ip=None)
    updated = await _service(session).update(
        application.id,
        ApplicationUpdate(current_stage_id=None, rejection_reason="Fuera de rango salarial."),
        actor,
    )
    assert updated.rejected_at_stage_id is None


async def test_reject_without_reason_is_rejected_with_400_equivalent(
    session: AsyncSession,
) -> None:
    """A manual reject (real transition into 'rejected') with no reason must be
    refused — the service raises RejectionReasonRequiredError, which the route
    maps to HTTP 400."""
    param, vacancy, stage, candidate = await _graph(session)
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=stage.id,
        )
    )
    await session.flush()

    actor = CurrentUser(user_id=1, ip=None)
    with pytest.raises(RejectionReasonRequiredError):
        await _service(session).update(
            application.id, ApplicationUpdate(current_stage_id=None), actor
        )


async def test_reject_already_rejected_does_not_require_reason(session: AsyncSession) -> None:
    """Re-submitting current_stage_id=None on an already-rejected application is a
    no-op transition (current_stage_id is already None) — it must not demand a
    reason again."""
    param, vacancy, stage, candidate = await _graph(session)
    rejected = await ParameterRepository(session).get_by_type_and_code(
        "application_status", "rejected"
    )
    assert rejected is not None
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=rejected.id,
            current_stage_id=None,
            rejected_at_stage_id=stage.id,
        )
    )
    await session.flush()

    actor = CurrentUser(user_id=1, ip=None)
    updated = await _service(session).update(
        application.id, ApplicationUpdate(current_stage_id=None), actor
    )
    assert updated.status_id == rejected.id


async def test_http_manual_reject_without_reason_returns_400(session: AsyncSession) -> None:
    """End-to-end: PATCH .../applications/{id} moving to the rejected column
    (current_stage_id=None) with no rejection_reason must answer 400 — not the
    generic 422/500 a missing-but-optional field would otherwise produce."""
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_session
    from app.core.security import create_access_token
    from app.main import app
    from app.modules.auth.application.bootstrap_service import bootstrap_admin

    param, vacancy, stage, candidate = await _graph(session)
    application = await BaseRepository(session, Application).add(
        Application(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=param.id,
            current_stage_id=stage.id,
        )
    )
    await session.flush()
    admin = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")
    token = create_access_token(admin.user_id, extra_claims={"portal": "staff"})
    headers = {"Authorization": f"Bearer {token}"}

    async def _use_test_session():
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing_reason = await client.patch(
                f"/api/v1/recruitment/applications/{application.id}",
                json={"current_stage_id": None},
                headers=headers,
            )
            assert missing_reason.status_code == 400, missing_reason.text

            reason = "No cumple el nivel de inglés."
            with_reason = await client.patch(
                f"/api/v1/recruitment/applications/{application.id}",
                json={"current_stage_id": None, "rejection_reason": reason},
                headers=headers,
            )
            assert with_reason.status_code == 200, with_reason.text
            assert with_reason.json()["rejection_reason"] == reason
    finally:
        app.dependency_overrides.clear()
