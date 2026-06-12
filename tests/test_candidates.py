import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.candidates_schemas import (
    CandidateCreate,
    CandidateUpdate,
)
from app.modules.recruitment.application.candidates_service import (
    CandidateReferenceError,
    CandidateService,
    DuplicateCandidateError,
)
from app.modules.recruitment.infrastructure.candidates_expanded import (
    CandidatesExpandedRepository,
)
from app.modules.recruitment.infrastructure.candidates_repository import (
    CandidateRepository,
)
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> CandidateService:
    return CandidateService(
        CandidateRepository(session),
        BaseRepository(session, User),
        BaseRepository(session, Parameter),
        BaseRepository(session, File),
        CandidatesExpandedRepository(session),
    )


async def _make_user(session: AsyncSession) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None
    return await BaseRepository(session, User).add(
        User(email=f"{uuid.uuid4().hex[:12]}@test.local", portal_id=portal.id)
    )


async def test_create_candidate_succeeds(session: AsyncSession) -> None:
    user = await _make_user(session)
    candidate = await _service(session).create(
        CandidateCreate(user_id=user.id, first_name="Juan", last_name="Perez"), ACTOR
    )

    assert candidate.id is not None
    assert candidate.user_id == user.id
    assert candidate.cv_embedding is None  # AI-managed, not set on create
    assert candidate.created_by == ACTOR.user_id


async def test_create_candidate_rejects_unknown_user(session: AsyncSession) -> None:
    with pytest.raises(CandidateReferenceError):
        await _service(session).create(
            CandidateCreate(user_id=999999, first_name="Ghost", last_name="User"), ACTOR
        )


async def test_create_candidate_rejects_second_for_same_user(
    session: AsyncSession,
) -> None:
    service = _service(session)
    user = await _make_user(session)
    await service.create(
        CandidateCreate(user_id=user.id, first_name="Juan", last_name="Perez"), ACTOR
    )

    with pytest.raises(DuplicateCandidateError):
        await service.create(
            CandidateCreate(user_id=user.id, first_name="Juan", last_name="Dup"), ACTOR
        )


async def test_create_candidate_rejects_duplicate_cedula(session: AsyncSession) -> None:
    service = _service(session)
    cedula = uuid.uuid4().hex[:10]
    first = await _make_user(session)
    second = await _make_user(session)
    await service.create(
        CandidateCreate(
            user_id=first.id, first_name="A", last_name="A", cedula=cedula
        ),
        ACTOR,
    )

    with pytest.raises(DuplicateCandidateError):
        await service.create(
            CandidateCreate(
                user_id=second.id, first_name="B", last_name="B", cedula=cedula
            ),
            ACTOR,
        )


async def test_create_candidate_rejects_unknown_file(session: AsyncSession) -> None:
    user = await _make_user(session)
    with pytest.raises(CandidateReferenceError):
        await _service(session).create(
            CandidateCreate(
                user_id=user.id, first_name="J", last_name="P", cv_file_id=999999
            ),
            ACTOR,
        )


async def test_update_candidate_validates_parameter(session: AsyncSession) -> None:
    service = _service(session)
    user = await _make_user(session)
    candidate = await service.create(
        CandidateCreate(user_id=user.id, first_name="Juan", last_name="Perez"), ACTOR
    )

    with pytest.raises(CandidateReferenceError):
        await service.update(candidate.id, CandidateUpdate(city_id=999999), ACTOR)


async def test_candidates_expanded_list_and_get(session: AsyncSession) -> None:
    user = await _make_user(session)
    candidate = await _service(session).create(
        CandidateCreate(user_id=user.id, first_name="Ana", last_name="Lopez"), ACTOR
    )

    repo = CandidatesExpandedRepository(session)
    items, total = await repo.list_expanded(PageParams(page=1, size=1000))
    assert any(i.id == candidate.id for i in items)
    assert total >= 1

    item = await repo.get_expanded(candidate.id)
    assert item is not None
    assert item.first_name == "Ana"
    assert item.last_name == "Lopez"
    assert item.email == user.email


async def test_candidates_expanded_filters_by_user_id(session: AsyncSession) -> None:
    user = await _make_user(session)
    candidate = await _service(session).create(
        CandidateCreate(user_id=user.id, first_name="Luis", last_name="Mora"), ACTOR
    )

    repo = CandidatesExpandedRepository(session)
    items, total = await repo.list_expanded(PageParams(page=1, size=50), user_id=user.id)
    assert total == 1
    assert len(items) == 1
    assert items[0].id == candidate.id

    items, total = await repo.list_expanded(PageParams(page=1, size=50), user_id=999999)
    assert total == 0
    assert items == []


async def test_candidates_expanded_paginates(session: AsyncSession) -> None:
    service = _service(session)
    for index in range(3):
        user = await _make_user(session)
        await service.create(
            CandidateCreate(user_id=user.id, first_name=f"P{index}", last_name="Paged"),
            ACTOR,
        )

    repo = CandidatesExpandedRepository(session)
    page_one, total = await repo.list_expanded(PageParams(page=1, size=2))
    page_two, _ = await repo.list_expanded(PageParams(page=2, size=2))

    assert total >= 3
    assert len(page_one) == 2
    assert {i.id for i in page_one}.isdisjoint({i.id for i in page_two})


async def test_candidates_expanded_get_nonexistent(session: AsyncSession) -> None:
    repo = CandidatesExpandedRepository(session)
    result = await repo.get_expanded(999999)
    assert result is None


async def test_create_candidate_with_university_and_address(session: AsyncSession) -> None:
    """New fields: university_id (FK to org.parameters) and home_address."""
    service = _service(session)
    user = await _make_user(session)

    # Fetch any university parameter seeded by the migration
    from sqlalchemy import select
    from app.modules.org.infrastructure.models import Parameter

    univ = (
        await session.execute(
            select(Parameter).where(Parameter.type == "university").limit(1)
        )
    ).scalar_one_or_none()
    assert univ is not None, "university catalog must be seeded"

    candidate = await service.create(
        CandidateCreate(
            user_id=user.id,
            first_name="Maria",
            last_name="Valverde",
            university_id=univ.id,
            home_address="Av. Amazonas N12-34, Quito",
        ),
        ACTOR,
    )

    assert candidate.university_id == univ.id
    assert candidate.home_address == "Av. Amazonas N12-34, Quito"


async def test_update_candidate_rejects_unknown_university(session: AsyncSession) -> None:
    service = _service(session)
    user = await _make_user(session)
    candidate = await service.create(
        CandidateCreate(user_id=user.id, first_name="Test", last_name="User"),
        ACTOR,
    )
    with pytest.raises(CandidateReferenceError):
        await service.update(candidate.id, CandidateUpdate(university_id=999999), ACTOR)


async def test_candidates_expanded_includes_university_and_address(
    session: AsyncSession,
) -> None:
    from sqlalchemy import select
    from app.modules.org.infrastructure.models import Parameter

    univ = (
        await session.execute(
            select(Parameter).where(Parameter.type == "university").limit(1)
        )
    ).scalar_one_or_none()
    assert univ is not None

    service = _service(session)
    user = await _make_user(session)
    candidate = await service.create(
        CandidateCreate(
            user_id=user.id,
            first_name="Expanded",
            last_name="Test",
            university_id=univ.id,
            home_address="Calle 10 y Av. 9",
        ),
        ACTOR,
    )

    repo = CandidatesExpandedRepository(session)
    expanded = await repo.get_expanded(candidate.id)
    assert expanded is not None
    assert expanded.university == univ.name
    assert expanded.home_address == "Calle 10 y Av. 9"
