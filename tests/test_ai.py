import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.ai.api.ai_usage_logs_schemas import AiUsageLogCreate
from app.modules.ai.api.cv_parse_jobs_schemas import CvParseJobCreate, CvParseJobUpdate
from app.modules.ai.api.vacancy_promo_images_schemas import VacancyPromoImageCreate
from app.modules.ai.application.ai_usage_logs_service import (
    AiUsageLogNotFoundError,
    AiUsageLogService,
)
from app.modules.ai.application.cv_parse_jobs_service import (
    CvParseJobNotFoundError,
    CvParseJobReferenceError,
    CvParseJobService,
)
from app.modules.ai.application.vacancy_promo_images_service import (
    VacancyPromoImageNotFoundError,
    VacancyPromoImageReferenceError,
    VacancyPromoImageService,
)
from app.modules.ai.infrastructure.models import AiUsageLog, CvParseJob, VacancyPromoImage
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _make_param(session: AsyncSession) -> Parameter:
    return await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="P")
    )


async def _make_file(session: AsyncSession) -> File:
    return await BaseRepository(session, File).add(
        File(original_name="f.pdf", stored_key=uuid.uuid4().hex, bucket="test")
    )


async def _make_candidate(session: AsyncSession) -> Candidate:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    user = await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )
    return await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="A", last_name="B")
    )


async def _make_vacancy(session: AsyncSession) -> Vacancy:
    p = await _make_param(session)
    company = await BaseRepository(session, ClientCompany).add(ClientCompany(name="CO"))
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="R",
            last_name="S",
            email=f"{uuid.uuid4().hex[:6]}@co.com",
        )
    )
    dept = await BaseRepository(session, Department).add(Department(name="D"))
    proc = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:4]}",
        )
    )
    return await BaseRepository(session, Vacancy).add(
        Vacancy(
            vacancy_name_id=p.id,
            client_company_id=company.id,
            contact_id=contact.id,
            department_id=dept.id,
            process_id=proc.id,
            career_id=p.id,
            city_id=p.id,
            work_mode_id=p.id,
            resource_level_id=p.id,
            status_id=p.id,
            created_by=1,
        )
    )


# ── CvParseJob ────────────────────────────────────────────────────────────────


def _cv_service(session: AsyncSession) -> CvParseJobService:
    return CvParseJobService(
        BaseRepository(session, CvParseJob),
        BaseRepository(session, File),
        BaseRepository(session, Candidate),
        BaseRepository(session, Parameter),
    )


async def test_create_cv_parse_job(session: AsyncSession) -> None:
    f = await _make_file(session)
    c = await _make_candidate(session)
    p = await _make_param(session)
    job = await _cv_service(session).create(
        CvParseJobCreate(file_id=f.id, candidate_id=c.id, status_id=p.id), ACTOR
    )

    assert job.id is not None
    assert job.result is None
    assert job.completed_at is None
    assert job.created_by == ACTOR.user_id


async def test_cv_job_rejects_unknown_file(session: AsyncSession) -> None:
    c = await _make_candidate(session)
    p = await _make_param(session)
    with pytest.raises(CvParseJobReferenceError):
        await _cv_service(session).create(
            CvParseJobCreate(file_id=999999, candidate_id=c.id, status_id=p.id), ACTOR
        )


async def test_update_cv_parse_job(session: AsyncSession) -> None:
    f = await _make_file(session)
    c = await _make_candidate(session)
    p = await _make_param(session)
    svc = _cv_service(session)
    job = await svc.create(
        CvParseJobCreate(file_id=f.id, candidate_id=c.id, status_id=p.id), ACTOR
    )
    updated = await svc.update(
        job.id,
        CvParseJobUpdate(result={"skills": ["python"]}, error_detail=None),
        ACTOR,
    )

    assert updated.result == {"skills": ["python"]}


async def test_cv_parse_job_not_found(session: AsyncSession) -> None:
    with pytest.raises(CvParseJobNotFoundError):
        await _cv_service(session).get(999999)


# ── VacancyPromoImage ─────────────────────────────────────────────────────────


def _promo_service(session: AsyncSession) -> VacancyPromoImageService:
    return VacancyPromoImageService(
        BaseRepository(session, VacancyPromoImage),
        BaseRepository(session, Vacancy),
        BaseRepository(session, File),
    )


async def test_create_promo_image(session: AsyncSession) -> None:
    v = await _make_vacancy(session)
    f = await _make_file(session)
    img = await _promo_service(session).create(
        VacancyPromoImageCreate(vacancy_id=v.id, file_id=f.id, template_used="banner_v1"),
        ACTOR,
    )

    assert img.id is not None
    assert img.template_used == "banner_v1"


async def test_promo_image_rejects_unknown_vacancy(session: AsyncSession) -> None:
    f = await _make_file(session)
    with pytest.raises(VacancyPromoImageReferenceError):
        await _promo_service(session).create(
            VacancyPromoImageCreate(vacancy_id=999999, file_id=f.id), ACTOR
        )


async def test_delete_promo_image(session: AsyncSession) -> None:
    v = await _make_vacancy(session)
    f = await _make_file(session)
    svc = _promo_service(session)
    img = await svc.create(VacancyPromoImageCreate(vacancy_id=v.id, file_id=f.id), ACTOR)
    await svc.delete(img.id)

    with pytest.raises(VacancyPromoImageNotFoundError):
        await svc.get(img.id)


# ── AiUsageLog ────────────────────────────────────────────────────────────────


def _log_service(session: AsyncSession) -> AiUsageLogService:
    return AiUsageLogService(BaseRepository(session, AiUsageLog))


async def test_create_ai_usage_log(session: AsyncSession) -> None:
    log = await _log_service(session).create(
        AiUsageLogCreate(
            action="cv_parse",
            model="gpt-4o",
            input_tokens=500,
            output_tokens=200,
            cost_usd=Decimal("0.003500"),
        ),
        ACTOR,
    )

    assert log.id is not None
    assert log.action == "cv_parse"
    assert log.cost_usd == Decimal("0.003500")


async def test_ai_usage_log_not_found(session: AsyncSession) -> None:
    with pytest.raises(AiUsageLogNotFoundError):
        await _log_service(session).get(999999)


async def test_list_ai_usage_logs_by_action(session: AsyncSession) -> None:
    svc = _log_service(session)
    await svc.create(AiUsageLogCreate(action="cv_parse"), ACTOR)
    await svc.create(AiUsageLogCreate(action="promo_gen"), ACTOR)

    items, total = await svc.list(PageParams(page=1, size=20), action="cv_parse")
    assert total >= 1
    assert all(i.action == "cv_parse" for i in items)
