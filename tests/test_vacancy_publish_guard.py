"""Slice 3: vacancy publish guards — solicitud-forcing + _guard_publish.

Business rules under test:
- R3: a caller WITHOUT recruitment.vacancies.publish gets their create payload
  overridden → status forced to 'solicitud', process_id forced to None.
- R4: _guard_publish blocks non-publishers (403), blocks publish-without-process
  (422), sets published_at on a valid active transition, and does NOT touch
  published_at when a non-status field is updated.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProfileTemplate,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.vacancies_schemas import VacancyCreate, VacancyUpdate
from app.modules.recruitment.application.vacancies_service import (
    VacancyProcessRequiredError,
    VacancyPublishForbiddenError,
    VacancyService,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.pipeline_repository import PipelineRepository
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")

PERM_PUBLISH = "recruitment.vacancies.publish"

CAN_PUBLISH: set[str] = {PERM_PUBLISH, "recruitment.vacancies.create"}
CANNOT_PUBLISH: set[str] = {"recruitment.vacancies.create"}


def _service(session: AsyncSession) -> VacancyService:
    return VacancyService(
        BaseRepository(session, Vacancy),
        BaseRepository(session, Parameter),
        BaseRepository(session, ClientCompany),
        BaseRepository(session, Contact),
        BaseRepository(session, Department),
        BaseRepository(session, Process),
        BaseRepository(session, ProfileTemplate),
        PipelineRepository(session),
    )


async def _status(session: AsyncSession, code: str) -> Parameter:
    p = await ParameterRepository(session).get_by_type_and_code("vacancy_status", code)
    assert p is not None, f"vacancy_status:{code} must be seeded"
    return p


async def _build_org(session: AsyncSession) -> dict:
    """Create a minimal org graph (company, contact, dept, process, params)."""
    p = await BaseRepository(session, Parameter).add(
        Parameter(type="x", code=uuid.uuid4().hex[:8], name="Param")
    )
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name=f"Co{uuid.uuid4().hex[:6]}")
    )
    contact = await BaseRepository(session, Contact).add(
        Contact(
            client_company_id=company.id,
            first_name="A",
            last_name="B",
            email=f"a{uuid.uuid4().hex[:6]}@test.co",
        )
    )
    dept = await BaseRepository(session, Department).add(
        Department(name=f"D{uuid.uuid4().hex[:6]}")
    )
    process = await BaseRepository(session, Process).add(
        Process(
            client_company_id=company.id,
            department_id=dept.id,
            name=f"P{uuid.uuid4().hex[:6]}",
        )
    )
    return {
        "param": p,
        "company": company,
        "contact": contact,
        "dept": dept,
        "process": process,
    }


async def _valid_payload(
    session: AsyncSession, org: dict, *, status_id: int, process_id: int | None
) -> VacancyCreate:
    o = org
    return VacancyCreate(
        vacancy_name_id=o["param"].id,
        client_company_id=o["company"].id,
        contact_id=o["contact"].id,
        department_id=o["dept"].id,
        process_id=process_id,
        career_id=o["param"].id,
        city_id=o["param"].id,
        work_mode_id=o["param"].id,
        resource_level_id=o["param"].id,
        status_id=status_id,
    )


# ── Task 3.1/3.2: solicitud-forcing on create ─────────────────────────────────


async def test_non_publisher_create_is_forced_to_solicitud(session: AsyncSession) -> None:
    """R3: caller without publish permission → status overridden to 'solicitud',
    process_id forced to None regardless of what the payload sent."""
    org = await _build_org(session)
    active = await _status(session, "active")
    solicitud = await _status(session, "solicitud")

    payload = await _valid_payload(session, org, status_id=active.id, process_id=org["process"].id)
    svc = _service(session)
    vacancy = await svc.create(payload, ACTOR, caller_permission_codes=CANNOT_PUBLISH)

    assert vacancy.status_id == solicitud.id, "status must be forced to 'solicitud'"
    assert vacancy.process_id is None, "process_id must be forced to None"


async def test_publisher_create_is_not_forced(session: AsyncSession) -> None:
    """R3 negative: caller WITH publish permission → payload passes as-is."""
    org = await _build_org(session)
    active = await _status(session, "active")

    payload = await _valid_payload(session, org, status_id=active.id, process_id=org["process"].id)
    svc = _service(session)
    vacancy = await svc.create(payload, ACTOR, caller_permission_codes=CAN_PUBLISH)

    assert vacancy.status_id == active.id, "publisher create must not be overridden"
    assert vacancy.process_id == org["process"].id


# ── Task 3.3-3.7: _guard_publish ──────────────────────────────────────────────


async def test_publish_attempt_by_non_publisher_raises_forbidden(session: AsyncSession) -> None:
    """R4: non-publisher trying to set status=active → VacancyPublishForbiddenError → 403."""
    org = await _build_org(session)
    solicitud = await _status(session, "solicitud")
    active = await _status(session, "active")

    # Create a solicitud vacancy first (as non-publisher)
    payload = await _valid_payload(
        session, org, status_id=solicitud.id, process_id=org["process"].id
    )
    svc = _service(session)
    vacancy = await svc.create(payload, ACTOR, caller_permission_codes=CAN_PUBLISH)

    # Now non-publisher tries to update to active → 403
    with pytest.raises(VacancyPublishForbiddenError):
        await svc.update(
            vacancy.id,
            VacancyUpdate(status_id=active.id),
            ACTOR,
            caller_permission_codes=CANNOT_PUBLISH,
        )


async def test_publisher_without_process_raises_process_required(session: AsyncSession) -> None:
    """R4: publisher sets active but process_id is None → VacancyProcessRequiredError → 422."""
    org = await _build_org(session)
    solicitud = await _status(session, "solicitud")
    active = await _status(session, "active")

    # Vacancy with no process_id
    payload = await _valid_payload(session, org, status_id=solicitud.id, process_id=None)
    svc = _service(session)
    vacancy = await svc.create(payload, ACTOR, caller_permission_codes=CAN_PUBLISH)

    # Publisher tries to set active with no process
    with pytest.raises(VacancyProcessRequiredError):
        await svc.update(
            vacancy.id,
            VacancyUpdate(status_id=active.id),
            ACTOR,
            caller_permission_codes=CAN_PUBLISH,
        )


async def test_publisher_with_process_sets_published_at(session: AsyncSession) -> None:
    """R4: valid publish → published_at is set (UTC, non-null)."""
    org = await _build_org(session)
    solicitud = await _status(session, "solicitud")
    active = await _status(session, "active")

    payload = await _valid_payload(
        session, org, status_id=solicitud.id, process_id=org["process"].id
    )
    svc = _service(session)
    vacancy = await svc.create(payload, ACTOR, caller_permission_codes=CAN_PUBLISH)
    assert vacancy.published_at is None, "published_at must be null before publishing"

    before = datetime.now(UTC)
    updated = await svc.update(
        vacancy.id,
        VacancyUpdate(status_id=active.id),
        ACTOR,
        caller_permission_codes=CAN_PUBLISH,
    )
    after = datetime.now(UTC)

    assert updated.published_at is not None, "published_at must be set after publish"
    assert before <= updated.published_at <= after, "published_at must be approximately now()"
    assert updated.status_id == active.id


async def test_non_status_update_does_not_touch_published_at(session: AsyncSession) -> None:
    """R4: updating a non-status field on an active vacancy must NOT rewrite published_at."""
    org = await _build_org(session)
    solicitud = await _status(session, "solicitud")
    active = await _status(session, "active")

    payload = await _valid_payload(
        session, org, status_id=solicitud.id, process_id=org["process"].id
    )
    svc = _service(session)
    vacancy = await svc.create(payload, ACTOR, caller_permission_codes=CAN_PUBLISH)

    # Publish it
    published = await svc.update(
        vacancy.id,
        VacancyUpdate(status_id=active.id),
        ACTOR,
        caller_permission_codes=CAN_PUBLISH,
    )
    original_published_at = published.published_at
    assert original_published_at is not None

    # Update a non-status field
    updated = await svc.update(
        published.id,
        VacancyUpdate(openings=5),
        ACTOR,
        caller_permission_codes=CAN_PUBLISH,
    )
    assert updated.published_at == original_published_at, (
        "published_at must not be overwritten on non-status updates"
    )


async def test_non_active_status_transition_does_not_set_published_at(
    session: AsyncSession,
) -> None:
    """R4: moving to 'paused' from solicitud must not set published_at."""
    org = await _build_org(session)
    solicitud = await _status(session, "solicitud")
    paused = await _status(session, "paused")

    payload = await _valid_payload(
        session, org, status_id=solicitud.id, process_id=org["process"].id
    )
    svc = _service(session)
    vacancy = await svc.create(payload, ACTOR, caller_permission_codes=CAN_PUBLISH)

    updated = await svc.update(
        vacancy.id,
        VacancyUpdate(status_id=paused.id),
        ACTOR,
        caller_permission_codes=CAN_PUBLISH,
    )
    assert updated.published_at is None, "published_at must remain None for non-active transitions"


# ── Task 3.8/3.9: HTTP-layer mapping via ASGI test client ─────────────────────


async def test_create_without_publish_permission_is_forced_via_route(session: AsyncSession) -> None:
    """Integration: the route inspects PermissionCodesDep and forces solicitud."""
    from collections.abc import AsyncGenerator

    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_session
    from app.core.security import create_access_token
    from app.main import app
    from app.modules.auth.application.bootstrap_service import (
        assign_role_to_user,
        bootstrap_admin,
        grant_permissions_to_role,
    )
    from app.modules.auth.infrastructure.models import Role, User
    from app.modules.auth.infrastructure.repository import UserRepository
    from app.modules.org.infrastructure.parameters_repository import ParameterRepository as PR

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

        # Make a non-publisher user (only create, no publish)
        portal = await PR(session).get_by_type_and_code("user_portal", "staff")
        assert portal is not None
        non_publisher = await UserRepository(session).add(
            User(email=f"{uuid.uuid4().hex[:12]}@staff.local", portal_id=portal.id)
        )
        # Create a role with vacancies.create but NOT publish
        thin_role = Role(name=f"ThinRole{uuid.uuid4().hex[:6]}", description="no publish")
        session.add(thin_role)
        await session.flush()
        await grant_permissions_to_role(
            session,
            thin_role.id,
            frozenset({"recruitment.vacancies.create", "recruitment.vacancies.read"}),
        )
        await assign_role_to_user(session, non_publisher.id, thin_role.id)
        await session.flush()

        org = await _build_org(session)
        active = await _status(session, "active")
        solicitud = await _status(session, "solicitud")

        token = create_access_token(non_publisher.id, extra_claims={"portal": "staff"})
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/recruitment/vacancies",
                json={
                    "vacancy_name_id": org["param"].id,
                    "client_company_id": org["company"].id,
                    "contact_id": org["contact"].id,
                    "department_id": org["dept"].id,
                    "process_id": org["process"].id,
                    "career_id": org["param"].id,
                    "city_id": org["param"].id,
                    "work_mode_id": org["param"].id,
                    "resource_level_id": org["param"].id,
                    "status_id": active.id,
                },
                headers=headers,
            )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["status_id"] == solicitud.id, "Non-publisher must be forced to solicitud"
        assert body["process_id"] is None, "Non-publisher process_id must be forced to None"
    finally:
        app.dependency_overrides.clear()


async def test_non_publisher_update_to_active_returns_403(session: AsyncSession) -> None:
    """R4: PATCH status=active by non-publisher → 403."""
    from collections.abc import AsyncGenerator

    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_session
    from app.core.security import create_access_token
    from app.main import app
    from app.modules.auth.application.bootstrap_service import (
        assign_role_to_user,
        bootstrap_admin,
        grant_permissions_to_role,
    )
    from app.modules.auth.infrastructure.models import Role, User
    from app.modules.auth.infrastructure.repository import UserRepository
    from app.modules.org.infrastructure.parameters_repository import ParameterRepository as PR

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

        portal = await PR(session).get_by_type_and_code("user_portal", "staff")
        assert portal is not None
        non_publisher = await UserRepository(session).add(
            User(email=f"{uuid.uuid4().hex[:12]}@staff.local", portal_id=portal.id)
        )
        thin_role = Role(name=f"ThinRole{uuid.uuid4().hex[:6]}", description="no publish")
        session.add(thin_role)
        await session.flush()
        await grant_permissions_to_role(
            session,
            thin_role.id,
            frozenset(
                {
                    "recruitment.vacancies.create",
                    "recruitment.vacancies.read",
                    "recruitment.vacancies.update",
                }
            ),
        )
        await assign_role_to_user(session, non_publisher.id, thin_role.id)
        await session.flush()

        org = await _build_org(session)
        solicitud = await _status(session, "solicitud")
        active = await _status(session, "active")

        # Create a vacancy in solicitud state via service (bypass route)
        svc = _service(session)
        payload = await _valid_payload(
            session, org, status_id=solicitud.id, process_id=org["process"].id
        )
        vacancy = await svc.create(payload, ACTOR, caller_permission_codes=CAN_PUBLISH)

        token = create_access_token(non_publisher.id, extra_claims={"portal": "staff"})
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.patch(
                f"/api/v1/recruitment/vacancies/{vacancy.id}",
                json={"status_id": active.id},
                headers=headers,
            )
        assert res.status_code == 403, res.text
    finally:
        app.dependency_overrides.clear()


async def test_publisher_without_process_returns_422(session: AsyncSession) -> None:
    """R4: publisher PATCH status=active with no process → 422."""
    from collections.abc import AsyncGenerator

    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_session
    from app.core.security import create_access_token
    from app.main import app
    from app.modules.auth.application.bootstrap_service import bootstrap_admin

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        result = await bootstrap_admin(session, f"{uuid.uuid4().hex[:12]}@test.local", "S3cret")

        org = await _build_org(session)
        solicitud = await _status(session, "solicitud")
        active = await _status(session, "active")

        # Create vacancy with no process_id
        svc = _service(session)
        vacancy = await svc.create(
            await _valid_payload(session, org, status_id=solicitud.id, process_id=None),
            ACTOR,
            caller_permission_codes=CAN_PUBLISH,
        )

        token = create_access_token(result.user_id, extra_claims={"portal": "staff"})
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.patch(
                f"/api/v1/recruitment/vacancies/{vacancy.id}",
                json={"status_id": active.id},
                headers=headers,
            )
        assert res.status_code == 422, res.text
    finally:
        app.dependency_overrides.clear()
