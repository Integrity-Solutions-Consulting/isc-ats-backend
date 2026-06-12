"""Row-level ownership: candidate-portal tokens may only touch their own rows.

RBAC (require_permission) grants the candidate role read/create/update on
candidates, applications and files — these tests pin that a candidate cannot
reach ANOTHER user's rows through those endpoints, and that staff tokens keep
full access (ownership scoping applies to portal == "candidate" only).
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.dependencies import CurrentUser
from app.core.security import create_access_token
from app.main import app
from app.modules.auth.application.bootstrap_service import (
    CANDIDATE_ROLE_NAME,
    assign_role_to_user,
    bootstrap_admin,
)
from app.modules.auth.infrastructure.models import Role, User
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.applications_schemas import ApplicationCreate
from app.modules.recruitment.application.applications_service import ApplicationService
from app.modules.recruitment.infrastructure.applications_repository import (
    ApplicationRepository,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.storage.infrastructure.models import File
from app.shared.repository import BaseRepository

CANDIDATES_URL = "/api/v1/recruitment/candidates"
APPLICATIONS_URL = "/api/v1/recruitment/applications"
FILES_URL = "/api/v1/storage/files"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _bearer(user_id: int, portal: str = "candidate") -> dict[str, str]:
    token = create_access_token(user_id, extra_claims={"portal": portal})
    return {"Authorization": f"Bearer {token}"}


async def _candidate_with_role(session: AsyncSession) -> tuple[User, Candidate]:
    """A candidate-portal user holding the bootstrapped candidate role + row."""
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None, "user_portal:candidate must be seeded"
    user = await UserRepository(session).add(
        User(email=f"{uuid.uuid4().hex[:12]}@cand.local", portal_id=portal.id)
    )
    role = (
        await session.execute(
            select(Role)
            .where(Role.name == CANDIDATE_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
    ).scalar_one()
    await assign_role_to_user(session, user.id, role.id)
    candidate = await BaseRepository(session, Candidate).add(
        Candidate(user_id=user.id, first_name="Test", last_name="Candidate")
    )
    return user, candidate


@pytest.fixture
async def two_candidates(
    session: AsyncSession,
) -> tuple[CurrentUser, tuple[User, Candidate], tuple[User, Candidate]]:
    """Bootstrapped admin + candidates A and B (each with role and row)."""
    admin = await bootstrap_admin(
        session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret"
    )
    a = await _candidate_with_role(session)
    b = await _candidate_with_role(session)
    return admin, a, b


async def _vacancy_graph(session: AsyncSession) -> tuple[Vacancy, Parameter]:
    """Minimal persisted vacancy + reusable parameter for status FKs."""
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
    return vacancy, param


def _applications_service(session: AsyncSession) -> ApplicationService:
    return ApplicationService(
        ApplicationRepository(session),
        BaseRepository(session, Vacancy),
        BaseRepository(session, Candidate),
        BaseRepository(session, ProcessStage),
        BaseRepository(session, Parameter),
    )


# ─── Candidates ───────────────────────────────────────────────────────────────


async def test_candidate_cannot_read_another_candidate(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (_, cand_a), (user_b, _) = two_candidates

    response = await client.get(
        f"{CANDIDATES_URL}/{cand_a.id}", headers=_bearer(user_b.id)
    )

    assert response.status_code == 403


async def test_candidate_can_read_own_candidate(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (user_a, cand_a), _ = two_candidates

    response = await client.get(
        f"{CANDIDATES_URL}/{cand_a.id}", headers=_bearer(user_a.id)
    )

    assert response.status_code == 200
    assert response.json()["id"] == cand_a.id


async def test_candidate_cannot_update_another_candidate(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (_, cand_a), (user_b, _) = two_candidates

    response = await client.patch(
        f"{CANDIDATES_URL}/{cand_a.id}",
        json={"first_name": "Hacked"},
        headers=_bearer(user_b.id),
    )

    assert response.status_code == 403


async def test_candidate_cannot_list_all_candidates(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, _, (user_b, _) = two_candidates

    response = await client.get(CANDIDATES_URL, headers=_bearer(user_b.id))

    assert response.status_code == 403


async def test_candidate_expanded_list_is_scoped_to_self(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    """Asking for another user_id must return the caller's own rows only."""
    _, (user_a, _), (user_b, _) = two_candidates

    response = await client.get(
        f"{CANDIDATES_URL}/expanded?user_id={user_a.id}",
        headers=_bearer(user_b.id),
    )

    assert response.status_code == 200
    assert all(item["user_id"] == user_b.id for item in response.json()["items"])


async def test_candidate_cannot_read_another_candidate_expanded(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (_, cand_a), (user_b, _) = two_candidates

    response = await client.get(
        f"{CANDIDATES_URL}/{cand_a.id}/expanded", headers=_bearer(user_b.id)
    )

    assert response.status_code == 403


async def test_candidate_cannot_create_candidate_for_another_user(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    """user_id in the payload must match the caller for candidate tokens."""
    _, (user_a, _), (user_b, _) = two_candidates

    response = await client.post(
        CANDIDATES_URL,
        json={"user_id": user_a.id, "first_name": "X", "last_name": "Y"},
        headers=_bearer(user_b.id),
    )

    assert response.status_code == 403


async def test_staff_can_still_read_any_candidate(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    admin, (_, cand_a), _ = two_candidates

    response = await client.get(
        f"{CANDIDATES_URL}/{cand_a.id}",
        headers=_bearer(admin.user_id, portal="staff"),
    )

    assert response.status_code == 200


# ─── Applications ─────────────────────────────────────────────────────────────


async def test_candidate_cannot_read_another_application(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (user_a, cand_a), (user_b, _) = two_candidates
    vacancy, param = await _vacancy_graph(session)
    application = await _applications_service(session).create(
        ApplicationCreate(vacancy_id=vacancy.id, candidate_id=cand_a.id, status_id=param.id),
        CurrentUser(user_id=user_a.id, ip="127.0.0.1"),
    )

    response = await client.get(
        f"{APPLICATIONS_URL}/{application.id}", headers=_bearer(user_b.id)
    )

    assert response.status_code == 403


async def test_candidate_cannot_apply_as_another_candidate(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (_, cand_a), (user_b, _) = two_candidates
    vacancy, param = await _vacancy_graph(session)

    response = await client.post(
        APPLICATIONS_URL,
        json={"vacancy_id": vacancy.id, "candidate_id": cand_a.id, "status_id": param.id},
        headers=_bearer(user_b.id),
    )

    assert response.status_code == 403


async def test_applications_list_is_scoped_to_own_candidate(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    """Filtering by another candidate_id must not leak their applications."""
    _, (user_a, cand_a), (user_b, cand_b) = two_candidates
    vacancy, param = await _vacancy_graph(session)
    await _applications_service(session).create(
        ApplicationCreate(vacancy_id=vacancy.id, candidate_id=cand_a.id, status_id=param.id),
        CurrentUser(user_id=user_a.id, ip="127.0.0.1"),
    )

    response = await client.get(
        f"{APPLICATIONS_URL}?candidate_id={cand_a.id}", headers=_bearer(user_b.id)
    )

    assert response.status_code == 200
    assert all(
        item["candidate_id"] == cand_b.id for item in response.json()["items"]
    )


# ─── Files ────────────────────────────────────────────────────────────────────


async def _file_owned_by(session: AsyncSession, owner: User) -> File:
    return await BaseRepository(session, File).add(
        File(
            original_name="cv.pdf",
            stored_key=uuid.uuid4().hex,
            bucket="test",
            entity_type="cv",
            created_by=owner.id,
        )
    )


async def test_candidate_cannot_read_anothers_file(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (user_a, _), (user_b, _) = two_candidates
    file = await _file_owned_by(session, user_a)

    response = await client.get(f"{FILES_URL}/{file.id}", headers=_bearer(user_b.id))

    assert response.status_code == 403


async def test_candidate_can_read_own_file(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (user_a, _), _ = two_candidates
    file = await _file_owned_by(session, user_a)

    response = await client.get(f"{FILES_URL}/{file.id}", headers=_bearer(user_a.id))

    assert response.status_code == 200


async def test_candidate_cannot_download_anothers_file(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, (user_a, _), (user_b, _) = two_candidates
    file = await _file_owned_by(session, user_a)

    response = await client.get(
        f"{FILES_URL}/{file.id}/download", headers=_bearer(user_b.id)
    )

    assert response.status_code == 403


async def test_candidate_cannot_list_files(
    client: AsyncClient, session: AsyncSession, two_candidates
) -> None:
    _, _, (user_b, _) = two_candidates

    response = await client.get(FILES_URL, headers=_bearer(user_b.id))

    assert response.status_code == 403
