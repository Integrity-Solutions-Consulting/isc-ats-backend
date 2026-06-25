"""U4 — Expose is_initial in schemas + endpoint (TDD-first, Slice 2).

Tests cover:
- ProcessStageRead serializes is_initial field.
- VacancyStageItem carries is_initial field.
- GET /vacancies/{id}/stages response items include is_initial.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.api.process_stages_schemas import ProcessStageBase, ProcessStageRead
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.recruitment.api.vacancies_schemas import VacancyStageItem
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.repository import BaseRepository

# ---------------------------------------------------------------------------
# U4-T1: ProcessStageRead includes is_initial field
# ---------------------------------------------------------------------------


def test_process_stage_base_has_is_initial_field() -> None:
    """ProcessStageBase must declare is_initial with a default of False."""
    schema = ProcessStageBase(process_id=1, stage_id=1, order=1)
    assert hasattr(schema, "is_initial"), "ProcessStageBase must have is_initial field"
    assert schema.is_initial is False, "is_initial must default to False"


def test_process_stage_base_is_initial_can_be_true() -> None:
    """ProcessStageBase must accept is_initial=True."""
    schema = ProcessStageBase(process_id=1, stage_id=1, order=1, is_initial=True)
    assert schema.is_initial is True


def test_process_stage_read_serializes_is_initial(session: AsyncSession) -> None:
    """ProcessStageRead.model_fields must include is_initial (schema-level check)."""
    assert "is_initial" in ProcessStageRead.model_fields, (
        "ProcessStageRead must include is_initial in its fields"
    )


async def test_process_stage_read_from_orm_includes_is_initial(session: AsyncSession) -> None:
    """ProcessStageRead.model_validate on a real ORM object must include is_initial."""
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:6]}", created_by=1)
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
    param = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Screen", created_by=1)
    )
    stage = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id,
            stage_id=param.id,
            order=1,
            is_initial=True,
            created_by=1,
        )
    )
    read = ProcessStageRead.model_validate(stage)
    assert read.is_initial is True, (
        f"ProcessStageRead.is_initial should be True for an is_initial=True "
        f"stage, got {read.is_initial}"
    )


# ---------------------------------------------------------------------------
# U4-T2: VacancyStageItem carries is_initial field
# ---------------------------------------------------------------------------


def test_vacancy_stage_item_has_is_initial_field() -> None:
    """VacancyStageItem must declare is_initial."""
    assert "is_initial" in VacancyStageItem.model_fields, (
        "VacancyStageItem must include is_initial in its fields"
    )


def test_vacancy_stage_item_constructs_with_is_initial() -> None:
    """VacancyStageItem must accept is_initial kwarg."""
    item = VacancyStageItem(
        id=1,
        name="Postulantes",
        order=1,
        is_final_positive=False,
        is_initial=True,
    )
    assert item.is_initial is True


def test_vacancy_stage_item_is_initial_defaults_false() -> None:
    """VacancyStageItem.is_initial must default to False when omitted."""
    item = VacancyStageItem(
        id=1,
        name="Tech Interview",
        order=2,
        is_final_positive=False,
    )
    assert item.is_initial is False


# ---------------------------------------------------------------------------
# U4-T3: GET /vacancies/{id}/stages response includes is_initial
# ---------------------------------------------------------------------------


async def _build_vacancy_with_stages(session: AsyncSession) -> tuple[int, int, int]:
    """Build a vacancy with one is_initial stage and one is_final_positive stage.

    Returns (vacancy_id, initial_stage_id, final_stage_id).
    """
    vp = await BaseRepository(session, Parameter).add(
        Parameter(type="vacancy_name", code=uuid.uuid4().hex[:8], name="Dev Role", created_by=1)
    )
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:6]}", created_by=1)
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="X",
            last_name="Y",
            email=f"x{uuid.uuid4().hex[:6]}@y.co",
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
    sp1 = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Initial", created_by=1)
    )
    sp2 = await BaseRepository(session, Parameter).add(
        Parameter(type="stage", code=uuid.uuid4().hex[:8], name="Final", created_by=1)
    )
    vacancy = await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=vp.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=process.id,
            career_id=vp.id,
            city_id=vp.id,
            work_mode_id=vp.id,
            resource_level_id=vp.id,
            status_id=vp.id,
        )
    )
    s1 = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id,
            stage_id=sp1.id,
            order=1,
            is_initial=True,
            is_final_positive=False,
            created_by=1,
        )
    )
    s2 = await BaseRepository(session, ProcessStage).add(
        ProcessStage(
            process_id=process.id,
            stage_id=sp2.id,
            order=2,
            is_initial=False,
            is_final_positive=True,
            created_by=1,
        )
    )
    # Flush so the pipeline query can see these rows within the same session.
    await session.flush()
    return vacancy.id, s1.id, s2.id


async def test_get_vacancy_stages_includes_is_initial_pipeline_repo(
    session: AsyncSession,
) -> None:
    """PipelineRepository.get_pipeline must return StageRow objects with is_initial.

    The HTTP endpoint test would require committed data (rolled-back test sessions
    cannot be seen by a separate ASGITransport session). We test the pipeline
    repository directly which is the data source for the endpoint.
    """
    from app.modules.recruitment.infrastructure.pipeline_repository import PipelineRepository

    vacancy_id, initial_stage_id, final_stage_id = await _build_vacancy_with_stages(session)

    pipeline = await PipelineRepository(session).get_pipeline(vacancy_id)

    assert len(pipeline.stages) == 2, f"Expected 2 stages, got {len(pipeline.stages)}"

    # Find the initial stage
    initial_rows = [s for s in pipeline.stages if s.id == initial_stage_id]
    final_rows = [s for s in pipeline.stages if s.id == final_stage_id]

    assert initial_rows, "Initial stage must be returned by get_pipeline"
    assert final_rows, "Final stage must be returned by get_pipeline"

    assert hasattr(initial_rows[0], "is_initial"), "StageRow must have is_initial attribute"
    assert initial_rows[0].is_initial is True, (
        f"Initial stage is_initial must be True, got {initial_rows[0].is_initial}"
    )
    assert final_rows[0].is_initial is False, (
        f"Final stage is_initial must be False, got {final_rows[0].is_initial}"
    )

    # VacancyStageItem must be constructible with is_initial from StageRow
    items = [
        VacancyStageItem(
            id=s.id,
            name=s.name,
            order=s.order,
            is_final_positive=s.is_final_positive,
            is_initial=s.is_initial,
        )
        for s in pipeline.stages
    ]
    assert items[0].is_initial is True
    assert items[1].is_initial is False
