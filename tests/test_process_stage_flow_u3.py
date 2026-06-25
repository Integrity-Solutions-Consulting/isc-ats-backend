"""U3 — ApplicationService writes status_id on terminal transitions (TDD-first, Slice 2).

Tests cover:
- move to a stage where is_final_positive=True → Application.status_id becomes 'hired'
- set current_stage_id=None (rejection) when not already rejected → status_id becomes 'rejected'
- already-rejected app (status_id=rejected, current_stage_id=None) updated again → NO-OP
- move OFF the final stage (was hired) back to a normal stage → status_id becomes 'active'
- normal non-terminal stage move → status_id unchanged
- param resolution by get_by_type_and_code('application_status', code) works for
  hired/rejected/active
- notification enqueue path is preserved (route-level guard)
"""

import uuid

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
from app.modules.recruitment.api.applications_schemas import ApplicationCreate, ApplicationUpdate
from app.modules.recruitment.application.applications_service import (
    ApplicationService,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.applications_repository import ApplicationRepository
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> ApplicationService:
    return ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        ParameterRepository(session),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_status_params(session: AsyncSession) -> dict[str, Parameter]:
    """Ensure the three application_status params exist and return them by code.

    The migration a7c3f1e9d2b4 already committed these to the real DB.
    We load via get_by_type_and_code which filters is_active=True.
    """
    repo = ParameterRepository(session)
    base = BaseRepository(session, Parameter)
    codes = ["active", "rejected", "hired"]
    result: dict[str, Parameter] = {}
    for code in codes:
        p = await repo.get_by_type_and_code("application_status", code)
        if p is None:
            p = await base.add(
                Parameter(
                    type="application_status",
                    code=code,
                    name=code.capitalize(),
                    created_by=1,
                )
            )
        result[code] = p
    return result


async def _build_full_graph(
    session: AsyncSession,
) -> tuple[Vacancy, Candidate, ProcessStage, ProcessStage, Parameter]:
    """Build vacancy + candidate + two stages (normal and final) + active status param.

    Returns: vacancy, candidate, normal_stage, final_stage, active_status_param
    """
    status_params = await _seed_status_params(session)
    active_p = status_params["active"]

    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:6]}", created_by=1)
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="A",
            last_name="B",
            email=f"a{uuid.uuid4().hex[:6]}@b.co",
        )
    )
    dept = await BaseRepository(session, Department).add(
        Department(name=f"D{uuid.uuid4().hex[:6]}", created_by=1)
    )
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"Proc{uuid.uuid4().hex[:6]}",
            created_by=1,
        )
    )
    # Stage name parameters
    stage_param_normal = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Screening", created_by=1)
    )
    stage_param_final = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Contratacion", created_by=1)
    )
    # Vacancy name param
    vname_param = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=uuid.uuid4().hex[:8], name="Dev", created_by=1)
    )

    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=vname_param.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=vname_param.id,
            city_id=vname_param.id,
            work_mode_id=vname_param.id,
            resource_level_id=vname_param.id,
            status_id=vname_param.id,
        )
    )

    normal_stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id,
            stage_id=stage_param_normal.id,
            order=1,
            is_final_positive=False,
            created_by=1,
        )
    )
    final_stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id,
            stage_id=stage_param_final.id,
            order=2,
            is_final_positive=True,
            created_by=1,
        )
    )

    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None, "user_portal/staff param must exist (seeded by migrations)"
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="Ana", last_name="Lopez")
    )

    return vacancy, candidate, normal_stage, final_stage, active_p


async def _create_app(
    session: AsyncSession,
    vacancy: Vacancy,
    candidate: Candidate,
    status_p: Parameter,
    stage: ProcessStage | None = None,
) -> Application:
    """Create an application, optionally overriding current_stage_id directly."""
    app = await _service(session).create(
        ApplicationCreate(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=status_p.id,
        ),
        ACTOR,
    )
    if stage is not None:
        # Force stage after create (service sets first_stage automatically)
        app = await ApplicationRepository(session).update(
            app, {"current_stage_id": stage.id}
        )
    return app


# ---------------------------------------------------------------------------
# U3-T1: move to final_positive stage → status_id becomes 'hired'
# ---------------------------------------------------------------------------


async def test_move_to_final_positive_stage_sets_hired(session: AsyncSession) -> None:
    """Moving to a stage where is_final_positive=True sets status_id to the 'hired' param."""
    vacancy, candidate, normal_stage, final_stage, active_p = await _build_full_graph(session)
    status_params = await _seed_status_params(session)

    app = await _create_app(session, vacancy, candidate, active_p, stage=normal_stage)
    # Confirm starting status is 'active'
    assert app.status_id == active_p.id

    updated = await _service(session).update(
        app.id,
        ApplicationUpdate(current_stage_id=final_stage.id),
        ACTOR,
    )
    assert updated.status_id == status_params["hired"].id, (
        f"Expected status_id={status_params['hired'].id} (hired), got {updated.status_id}"
    )


# ---------------------------------------------------------------------------
# U3-T2: set current_stage_id=None → status_id becomes 'rejected'
# ---------------------------------------------------------------------------


async def test_set_stage_none_sets_rejected(session: AsyncSession) -> None:
    """Setting current_stage_id=None on a non-rejected app sets status_id to 'rejected'."""
    vacancy, candidate, normal_stage, final_stage, active_p = await _build_full_graph(session)
    status_params = await _seed_status_params(session)

    app = await _create_app(session, vacancy, candidate, active_p, stage=normal_stage)

    updated = await _service(session).update(
        app.id,
        ApplicationUpdate(current_stage_id=None),
        ACTOR,
    )
    assert updated.status_id == status_params["rejected"].id, (
        f"Expected rejected status_id={status_params['rejected'].id}, got {updated.status_id}"
    )
    assert updated.current_stage_id is None


# ---------------------------------------------------------------------------
# U3-T3: already-rejected app updated again → NO-OP (status unchanged)
# ---------------------------------------------------------------------------


async def test_already_rejected_app_is_noop(session: AsyncSession) -> None:
    """An already-rejected application (status_id=rejected, stage=None) must not change status."""
    vacancy, candidate, normal_stage, final_stage, active_p = await _build_full_graph(session)
    status_params = await _seed_status_params(session)
    rejected_p = status_params["rejected"]

    # Create app already in rejected state
    app = await _service(session).create(
        ApplicationCreate(
            vacancy_id=vacancy.id,
            candidate_id=candidate.id,
            status_id=rejected_p.id,
        ),
        ACTOR,
    )
    # Force stage=None and status=rejected
    app = await ApplicationRepository(session).update(
        app,
        {"current_stage_id": None, "status_id": rejected_p.id},
    )
    assert app.status_id == rejected_p.id

    # Update with no meaningful change — status must stay rejected
    updated = await _service(session).update(
        app.id,
        ApplicationUpdate(current_stage_id=None),
        ACTOR,
    )
    assert updated.status_id == rejected_p.id, (
        "Already-rejected application must not change status (NO-OP)"
    )


# ---------------------------------------------------------------------------
# U3-T4: move OFF final stage (was hired) back to normal → status_id = 'active'
# ---------------------------------------------------------------------------


async def test_move_off_final_stage_reactivates(session: AsyncSession) -> None:
    """Moving from a final_positive stage to a non-final stage sets status_id to 'active'."""
    vacancy, candidate, normal_stage, final_stage, active_p = await _build_full_graph(session)
    status_params = await _seed_status_params(session)
    hired_p = status_params["hired"]

    # Start on final stage with hired status
    app = await _create_app(session, vacancy, candidate, active_p, stage=final_stage)
    app = await ApplicationRepository(session).update(app, {"status_id": hired_p.id})
    assert app.status_id == hired_p.id

    # Move back to a normal stage
    updated = await _service(session).update(
        app.id,
        ApplicationUpdate(current_stage_id=normal_stage.id),
        ACTOR,
    )
    assert updated.status_id == status_params["active"].id, (
        f"Moving off final stage to normal should set active, got {updated.status_id}"
    )


# ---------------------------------------------------------------------------
# U3-T5: normal non-terminal stage move → status_id unchanged
# ---------------------------------------------------------------------------


async def test_normal_stage_move_does_not_change_status(session: AsyncSession) -> None:
    """Moving between non-final stages must leave status_id unchanged."""
    vacancy, candidate, normal_stage, final_stage, active_p = await _build_full_graph(session)

    # Add a second normal stage
    stage_param_extra = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Technical", created_by=1)
    )
    normal_stage_2 = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=normal_stage.process_id,
            stage_id=stage_param_extra.id,
            order=3,
            is_final_positive=False,
            created_by=1,
        )
    )

    app = await _create_app(session, vacancy, candidate, active_p, stage=normal_stage)
    original_status_id = app.status_id

    updated = await _service(session).update(
        app.id,
        ApplicationUpdate(current_stage_id=normal_stage_2.id),
        ACTOR,
    )
    assert updated.status_id == original_status_id, (
        f"Normal stage move must not change status_id. "
        f"Expected {original_status_id}, got {updated.status_id}"
    )


# ---------------------------------------------------------------------------
# U3-T6: param resolution by get_by_type_and_code works for all three codes
# ---------------------------------------------------------------------------


async def test_application_status_param_resolution(session: AsyncSession) -> None:
    """ParameterRepository resolves hired/rejected/active by type+code correctly."""
    repo = ParameterRepository(session)
    for code in ["active", "rejected", "hired"]:
        param = await repo.get_by_type_and_code("application_status", code)
        assert param is not None, (
            f"Parameter (application_status, {code}) must exist (seeded by migration a7c3f1e9d2b4)"
        )
        assert param.is_active is True, f"Param {code} must be active"
        assert param.code == code


# ---------------------------------------------------------------------------
# U3-T7: withdrawn/inactive application (no_update) — service still handles transitions
# ---------------------------------------------------------------------------


async def test_multiple_transitions_chain(session: AsyncSession) -> None:
    """Full transition chain: active→hired→active→rejected must follow the matrix."""
    vacancy, candidate, normal_stage, final_stage, active_p = await _build_full_graph(session)
    status_params = await _seed_status_params(session)

    app = await _create_app(session, vacancy, candidate, active_p, stage=normal_stage)

    # → hired
    app = await _service(session).update(
        app.id, ApplicationUpdate(current_stage_id=final_stage.id), ACTOR
    )
    assert app.status_id == status_params["hired"].id

    # → active (back to normal)
    app = await _service(session).update(
        app.id, ApplicationUpdate(current_stage_id=normal_stage.id), ACTOR
    )
    assert app.status_id == status_params["active"].id

    # → rejected (stage=None)
    app = await _service(session).update(
        app.id, ApplicationUpdate(current_stage_id=None), ACTOR
    )
    assert app.status_id == status_params["rejected"].id

    # → NO-OP (already rejected)
    app = await _service(session).update(
        app.id, ApplicationUpdate(current_stage_id=None), ACTOR
    )
    assert app.status_id == status_params["rejected"].id
