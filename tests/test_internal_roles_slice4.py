"""Slice 4 — Notify fan-out to recruitment.vacancies.publish holders on solicitud creation.

Tasks 4.1-4.8 (spec R7), updated for the permission-based fan-out (was role-name
based — notify_role(role_name="Talento Humano") hardcoded a single role; now
notify_permission_holders(permission_code="recruitment.vacancies.publish") notifies
whoever holds the permission, so a future role granted it needs no code change):
  4.1  notify_permission_holders signature importable from notifications_service
  4.2  notify_permission_holders inserts N in-app Notifications — only active
       publish-permission holders (integration)
  4.3  notify_permission_holders dispatches one email per notified user when
       email_render provided
  4.4  SMTPException on one user is swallowed; others still notified (no 5xx)
  4.5  render_solicitud_created_email is importable from email_templates
  4.6  render_solicitud_created_email returns non-empty HTML containing the vacancy title
  4.7  _notify_solicitud_created is registered as a task (name: "notify_solicitud_created")
  4.8  create_vacancy fires background task only for solicitud, not for active/publisher path

All async tests use a rolled-back session; email dispatch uses a stub sender.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# 4.1 — notify_permission_holders is importable with the correct signature
# ---------------------------------------------------------------------------


def test_notify_permission_holders_is_importable() -> None:
    """notify_permission_holders must be importable from notifications_service."""
    import inspect  # noqa: PLC0415

    from app.modules.comms.application.notifications_service import (  # noqa: PLC0415
        notify_permission_holders,
    )

    assert callable(notify_permission_holders)
    sig = inspect.signature(notify_permission_holders)
    params = list(sig.parameters)
    # First param is the session (positional); rest are keyword-only
    assert "session" in params
    assert "permission_code" in params
    assert "title" in params
    assert "body" in params
    assert "related_entity_type" in params
    assert "related_entity_id" in params
    assert "email_render" in params


def test_talento_humano_role_name_constant_importable() -> None:
    """TALENTO_HUMANO_ROLE_NAME constant must exist in bootstrap_service."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_ROLE_NAME,
    )

    assert isinstance(TALENTO_HUMANO_ROLE_NAME, str)
    assert TALENTO_HUMANO_ROLE_NAME == "Talento Humano"


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


async def _seed_th_users(
    session: AsyncSession,
    *,
    active_count: int,
    inactive_count: int,
) -> tuple[list[Any], list[Any]]:
    """Seed 'Talento Humano' role + users, return (active_users, inactive_users).

    Uses the existing bootstrap_admin helper to ensure the TH role exists, then
    creates real auth.users and auth.user_roles rows inside the rolled-back session.
    """
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        TALENTO_HUMANO_ROLE_NAME,
        bootstrap_admin,
    )
    from app.modules.auth.infrastructure.models import Role, User, UserRole  # noqa: PLC0415

    # Bootstrap so TH role exists (idempotent)
    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:8]}@test.local", "S3cret")

    th_role = (
        await session.execute(
            select(Role)
            .where(Role.name == TALENTO_HUMANO_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
    ).scalar_one()

    # Resolve staff portal parameter
    from app.modules.org.infrastructure.parameters_repository import (
        ParameterRepository,  # noqa: PLC0415
    )

    staff_portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert staff_portal is not None, "user_portal:staff parameter not found — run migrations"

    active_users: list[Any] = []
    inactive_users: list[Any] = []

    for i in range(active_count):
        tag = uuid.uuid4().hex[:8]
        u = User(
            email=f"th-active-{i}-{tag}@test.local",
            portal_id=staff_portal.id,
            email_verified=True,
            is_active=True,
        )
        session.add(u)
        await session.flush()
        ur = UserRole(user_id=u.id, role_id=th_role.id, is_active=True)
        session.add(ur)
        await session.flush()
        active_users.append(u)

    for i in range(inactive_count):
        tag = uuid.uuid4().hex[:8]
        u = User(
            email=f"th-inactive-{i}-{tag}@test.local",
            portal_id=staff_portal.id,
            email_verified=True,
            is_active=False,
        )
        session.add(u)
        await session.flush()
        ur = UserRole(user_id=u.id, role_id=th_role.id, is_active=True)
        session.add(ur)
        await session.flush()
        inactive_users.append(u)

    await session.flush()
    return active_users, inactive_users


# ---------------------------------------------------------------------------
# 4.2 — notify_permission_holders inserts exactly N Notifications for active
# recruitment.vacancies.publish holders
# ---------------------------------------------------------------------------


async def test_notify_permission_holders_notifies_active_users_only(
    session: AsyncSession,
) -> None:
    """4 active TH + 2 inactive TH → exactly 4 in-app Notifications (spec R7.5/R7.6).

    TH is used here only as a convenient role that holds
    recruitment.vacancies.publish — the function itself resolves by permission,
    not by role name (see test_notify_permission_holders_excludes_other_roles for
    the exclusion side of this).
    """
    from app.modules.comms.application.notifications_service import (  # noqa: PLC0415
        notify_permission_holders,
    )
    from app.modules.comms.infrastructure.models import Notification  # noqa: PLC0415

    active, _inactive = await _seed_th_users(session, active_count=4, inactive_count=2)

    count = await notify_permission_holders(
        session,
        permission_code="recruitment.vacancies.publish",
        title="Nueva solicitud",
        body="Se creó una nueva solicitud de vacante.",
        related_entity_type="vacancy",
        related_entity_id=999,
    )

    assert count == 4, f"Expected 4 notifications, got {count}"

    # Verify actual rows in DB (within the rolled-back session)
    active_ids = {u.id for u in active}
    rows = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.related_entity_type == "vacancy",
                    Notification.related_entity_id == 999,
                )
            )
        )
        .scalars()
        .all()
    )

    assert len(rows) == 4, f"Expected 4 Notification rows, got {len(rows)}"
    row_recipient_ids = {r.recipient_id for r in rows}
    assert row_recipient_ids == active_ids, (
        f"Notification recipients must be exactly the active TH users. "
        f"Expected {active_ids}, got {row_recipient_ids}"
    )


async def test_notify_permission_holders_zero_active_users(session: AsyncSession) -> None:
    """0 active + 2 inactive TH → 0 notifications, no error (spec R7.5)."""
    from app.modules.comms.application.notifications_service import (  # noqa: PLC0415
        notify_permission_holders,
    )

    await _seed_th_users(session, active_count=0, inactive_count=2)

    count = await notify_permission_holders(
        session,
        permission_code="recruitment.vacancies.publish",
        title="Nueva solicitud",
        body="Se creó una nueva solicitud de vacante.",
        related_entity_type="vacancy",
        related_entity_id=888,
    )

    assert count == 0, f"Expected 0 notifications for 0 active users, got {count}"


async def test_notify_permission_holders_excludes_other_roles(session: AsyncSession) -> None:
    """Comercial/Proyecto (no recruitment.vacancies.publish) must NOT be notified (spec R7.6)."""
    from app.modules.auth.application.bootstrap_service import (  # noqa: PLC0415
        bootstrap_admin,
    )
    from app.modules.auth.infrastructure.models import Role, User, UserRole  # noqa: PLC0415
    from app.modules.comms.application.notifications_service import (  # noqa: PLC0415
        notify_permission_holders,
    )
    from app.modules.comms.infrastructure.models import Notification  # noqa: PLC0415
    from app.modules.org.infrastructure.parameters_repository import (
        ParameterRepository,  # noqa: PLC0415
    )

    await bootstrap_admin(session, f"admin-{uuid.uuid4().hex[:8]}@test.local", "S3cret")

    staff_portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")

    comercial_role = (
        await session.execute(
            select(Role).where(Role.name == "Comercial").where(Role.is_active.is_(True))
        )
    ).scalar_one()

    tag = uuid.uuid4().hex[:8]
    comercial_user = User(
        email=f"comercial-{tag}@test.local",
        portal_id=staff_portal.id,
        email_verified=True,
        is_active=True,
    )
    session.add(comercial_user)
    await session.flush()
    session.add(UserRole(user_id=comercial_user.id, role_id=comercial_role.id, is_active=True))
    await session.flush()

    # 1 active TH user
    active, _ = await _seed_th_users(session, active_count=1, inactive_count=0)

    count = await notify_permission_holders(
        session,
        permission_code="recruitment.vacancies.publish",
        title="Nueva solicitud",
        body="body",
        related_entity_type="vacancy",
        related_entity_id=777,
    )

    assert count == 1, f"Only 1 TH user must be notified, got {count}"

    rows = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.related_entity_type == "vacancy",
                    Notification.related_entity_id == 777,
                )
            )
        )
        .scalars()
        .all()
    )

    recipient_ids = {r.recipient_id for r in rows}
    assert comercial_user.id not in recipient_ids, "Comercial user must NOT receive TH notification"
    assert active[0].id in recipient_ids, "Active TH user must receive notification"


# ---------------------------------------------------------------------------
# 4.3 — notify_permission_holders dispatches emails when email_render is provided
# ---------------------------------------------------------------------------


async def test_notify_permission_holders_dispatches_emails(session: AsyncSession) -> None:
    """4 active TH users → 4 email dispatch calls when email_render is provided (spec R7.4)."""
    from unittest.mock import patch  # noqa: PLC0415

    from app.modules.comms.application.email_templates import (
        render_solicitud_created_email,  # noqa: PLC0415
    )
    from app.modules.comms.application.notifications_service import (  # noqa: PLC0415
        notify_permission_holders,
    )

    await _seed_th_users(session, active_count=4, inactive_count=0)

    sent_to: list[str] = []

    async def _mock_send(msg: Any) -> bool:
        sent_to.append(msg.to_email)
        return True

    with patch(
        "app.modules.comms.application.notifications_service.EmailDispatchService"
    ) as MockDispatch:
        instance = MagicMock()
        instance.send = AsyncMock(side_effect=_mock_send)
        MockDispatch.return_value = instance

        count = await notify_permission_holders(
            session,
            permission_code="recruitment.vacancies.publish",
            title="Nueva solicitud",
            body="body",
            related_entity_type="vacancy",
            related_entity_id=666,
            email_render=lambda email, title: render_solicitud_created_email(email, title),
        )

    assert count == 4
    assert len(sent_to) == 4, f"Expected 4 emails, got {len(sent_to)}"


# ---------------------------------------------------------------------------
# 4.4 — SMTPException on one user is swallowed; no crash, other users notified
# ---------------------------------------------------------------------------


async def test_notify_permission_holders_smtp_failure_on_one_user_swallowed(
    session: AsyncSession,
) -> None:
    """SMTP failure on user 2 must be swallowed; user 1 still notified (spec R7.7)."""
    from unittest.mock import patch  # noqa: PLC0415

    from app.modules.comms.application.email_templates import (
        render_solicitud_created_email,  # noqa: PLC0415
    )
    from app.modules.comms.application.notifications_service import (  # noqa: PLC0415
        notify_permission_holders,
    )
    from app.modules.comms.infrastructure.models import Notification  # noqa: PLC0415

    active, _ = await _seed_th_users(session, active_count=2, inactive_count=0)

    call_count = 0

    async def _failing_send(msg: Any) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("SMTP failure on second user")
        return True

    with patch(
        "app.modules.comms.application.notifications_service.EmailDispatchService"
    ) as MockDispatch:
        instance = MagicMock()
        instance.send = AsyncMock(side_effect=_failing_send)
        MockDispatch.return_value = instance

        # Must NOT raise
        count = await notify_permission_holders(
            session,
            permission_code="recruitment.vacancies.publish",
            title="Nueva solicitud",
            body="body",
            related_entity_type="vacancy",
            related_entity_id=555,
            email_render=lambda email, title: render_solicitud_created_email(email, title),
        )

    # Both in-app notifications must have been inserted despite email failure
    assert count == 2, f"Expected 2 in-app notifications, got {count}"
    rows = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.related_entity_type == "vacancy",
                    Notification.related_entity_id == 555,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2, "Both in-app notifications must persist even when email fails"


# ---------------------------------------------------------------------------
# 4.5 — render_solicitud_created_email is importable
# ---------------------------------------------------------------------------


def test_render_solicitud_created_email_is_importable() -> None:
    """render_solicitud_created_email must be importable from email_templates."""
    import inspect  # noqa: PLC0415

    from app.modules.comms.application.email_templates import (  # noqa: PLC0415
        render_solicitud_created_email,
    )

    assert callable(render_solicitud_created_email)
    sig = inspect.signature(render_solicitud_created_email)
    params = list(sig.parameters)
    # Expected: (recipient_email, vacancy_title) or similar
    assert len(params) == 2


# ---------------------------------------------------------------------------
# 4.6 — render_solicitud_created_email returns non-empty HTML with vacancy title
# ---------------------------------------------------------------------------


def test_render_solicitud_created_email_contains_vacancy_title() -> None:
    """HTML body must be non-empty and include the vacancy title (spec R7)."""
    from app.modules.comms.application.email_templates import (  # noqa: PLC0415
        render_solicitud_created_email,
    )

    rendered = render_solicitud_created_email("th@test.local", "Desarrollador Senior")
    assert rendered.html_body, "html_body must not be empty"
    assert "Desarrollador Senior" in rendered.html_body, (
        "html_body must contain the vacancy title 'Desarrollador Senior'"
    )
    assert rendered.subject, "subject must not be empty"
    assert rendered.text_body, "text_body must not be empty"


def test_render_solicitud_created_email_subject_non_empty() -> None:
    """Subject must be a non-empty string."""
    from app.modules.comms.application.email_templates import (  # noqa: PLC0415
        render_solicitud_created_email,
    )

    rendered = render_solicitud_created_email("th@test.local", "Analista de Datos")
    assert len(rendered.subject) > 0


# ---------------------------------------------------------------------------
# 4.7 — _notify_solicitud_created is registered as a task
# ---------------------------------------------------------------------------


def test_notify_solicitud_created_task_is_registered() -> None:
    """'notify_solicitud_created' must be registered in the task registry."""
    # Importing the route module triggers register_task at import time.
    import app.modules.recruitment.api.vacancies_routes  # noqa: F401, PLC0415
    from app.core.task_queue import get_task  # noqa: PLC0415

    task = get_task("notify_solicitud_created")
    assert callable(task)


# ---------------------------------------------------------------------------
# 4.8 — create_vacancy enqueues task for solicitud, NOT for publisher path
# ---------------------------------------------------------------------------


async def test_create_vacancy_enqueues_notify_task_for_solicitud(session: AsyncSession) -> None:
    """Non-publisher create → solicitud → task 'notify_solicitud_created' is enqueued."""
    import uuid as _uuid  # noqa: PLC0415
    from collections.abc import AsyncGenerator  # noqa: PLC0415

    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415

    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.auth.infrastructure.models import Role, User, UserRole  # noqa: PLC0415
    from app.modules.org.infrastructure.models import (  # noqa: PLC0415
        ClientCompany,
        Contact,
        Department,
        Parameter,
    )
    from app.modules.org.infrastructure.parameters_repository import (
        ParameterRepository,  # noqa: PLC0415
    )
    from app.shared.repository import BaseRepository  # noqa: PLC0415

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session

    enqueued: list[tuple] = []

    class _TrackingQueue:
        async def enqueue(self, task_name: str, *args: object) -> None:
            enqueued.append((task_name, *args))

    app.state.task_queue = _TrackingQueue()

    try:
        # Bootstrap so Comercial role exists
        await bootstrap_admin(session, f"admin-{_uuid.uuid4().hex[:8]}@test.local", "S3cret")

        # Create a Comercial user (no publish permission)
        params_repo = ParameterRepository(session)
        staff_portal = await params_repo.get_by_type_and_code("user_portal", "staff")

        comercial_role = (
            await session.execute(
                select(Role).where(Role.name == "Comercial").where(Role.is_active.is_(True))
            )
        ).scalar_one()

        tag = _uuid.uuid4().hex[:8]
        comercial_user = User(
            email=f"comercial-4.8-{tag}@test.local",
            portal_id=staff_portal.id,
            email_verified=True,
            is_active=True,
        )
        session.add(comercial_user)
        await session.flush()
        session.add(UserRole(user_id=comercial_user.id, role_id=comercial_role.id, is_active=True))
        await session.flush()

        # Seed required catalog parameters
        async def _param(type_: str, code: str, name: str) -> Parameter:
            existing = await params_repo.get_by_type_and_code(type_, code)
            if existing:
                return existing
            p = Parameter(type=type_, code=code, name=name, is_active=True)
            session.add(p)
            await session.flush()
            return p

        vn = await _param("vacancy_name", f"vn-{tag}", "Analista")
        career = await _param("career", f"car-{tag}", "IT")
        city = await _param("city", f"city-{tag}", "Quito")
        wm = await _param("work_mode", f"wm-{tag}", "Remote")
        rl = await _param("resource_level", f"rl-{tag}", "Mid")

        company = await BaseRepository(session, ClientCompany).add(ClientCompany(name=f"Co-{tag}"))
        contact = await BaseRepository(session, Contact).add(
            Contact(
                client_company_id=company.id,
                first_name="A",
                last_name="B",
                email=f"contact-{tag}@test.local",
            )
        )
        dept = await BaseRepository(session, Department).add(Department(name=f"Dept-{tag}"))
        await session.flush()

        # Provide any status_id in the body — the service will override it to
        # "solicitud" because the Comercial caller lacks the publish permission.
        solicitud_status = await params_repo.get_by_type_and_code("vacancy_status", "solicitud")
        assert solicitud_status is not None, "vacancy_status:solicitud must exist (run migrations)"

        token = create_access_token(comercial_user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/recruitment/vacancies",
                json={
                    "vacancy_name_id": vn.id,
                    "client_company_id": company.id,
                    "contact_id": contact.id,
                    "department_id": dept.id,
                    "career_id": career.id,
                    "city_id": city.id,
                    "work_mode_id": wm.id,
                    "resource_level_id": rl.id,
                    "status_id": solicitud_status.id,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

        enqueued_tasks = [e[0] for e in enqueued]
        assert "notify_solicitud_created" in enqueued_tasks, (
            f"'notify_solicitud_created' task must be enqueued for solicitud. "
            f"Enqueued: {enqueued_tasks}"
        )
    finally:
        app.dependency_overrides.clear()


async def test_create_vacancy_does_not_enqueue_notify_for_active_vacancy(
    session: AsyncSession,
) -> None:
    """Publisher create → active vacancy → no 'notify_solicitud_created' task enqueued."""
    import uuid as _uuid  # noqa: PLC0415
    from collections.abc import AsyncGenerator  # noqa: PLC0415

    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415

    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.org.infrastructure.models import (  # noqa: PLC0415
        ClientCompany,
        Contact,
        Department,
        Parameter,
        Process,
    )
    from app.modules.org.infrastructure.parameters_repository import (
        ParameterRepository,  # noqa: PLC0415
    )
    from app.shared.repository import BaseRepository  # noqa: PLC0415

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session

    enqueued: list[tuple] = []

    class _TrackingQueue:
        async def enqueue(self, task_name: str, *args: object) -> None:
            enqueued.append((task_name, *args))

    app.state.task_queue = _TrackingQueue()

    try:
        admin_result = await bootstrap_admin(
            session, f"admin-{_uuid.uuid4().hex[:8]}@test.local", "S3cret"
        )

        params_repo = ParameterRepository(session)
        tag = _uuid.uuid4().hex[:8]

        async def _param(type_: str, code: str, name: str) -> Parameter:
            existing = await params_repo.get_by_type_and_code(type_, code)
            if existing:
                return existing
            p = Parameter(type=type_, code=code, name=name, is_active=True)
            session.add(p)
            await session.flush()
            return p

        vn = await _param("vacancy_name", f"vn2-{tag}", "Dev")
        career = await _param("career", f"car2-{tag}", "Engineering")
        city = await _param("city", f"city2-{tag}", "Guayaquil")
        wm = await _param("work_mode", f"wm2-{tag}", "Hybrid")
        rl = await _param("resource_level", f"rl2-{tag}", "Senior")
        active_status = await params_repo.get_by_type_and_code("vacancy_status", "active")
        assert active_status is not None, "vacancy_status:active must exist"

        company = await BaseRepository(session, ClientCompany).add(ClientCompany(name=f"Co2-{tag}"))
        contact = await BaseRepository(session, Contact).add(
            Contact(
                client_company_id=company.id,
                first_name="X",
                last_name="Y",
                email=f"contact2-{tag}@test.local",
            )
        )
        dept = await BaseRepository(session, Department).add(Department(name=f"Dept2-{tag}"))
        process = await BaseRepository(session, Process).add(
            Process(name=f"Proc-{tag}", department_id=dept.id, client_company_id=company.id)
        )
        await session.flush()

        token = create_access_token(admin_result.user_id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/recruitment/vacancies",
                json={
                    "vacancy_name_id": vn.id,
                    "client_company_id": company.id,
                    "contact_id": contact.id,
                    "department_id": dept.id,
                    "career_id": career.id,
                    "city_id": city.id,
                    "work_mode_id": wm.id,
                    "resource_level_id": rl.id,
                    "status_id": active_status.id,
                    "process_id": process.id,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

        enqueued_tasks = [e[0] for e in enqueued]
        assert "notify_solicitud_created" not in enqueued_tasks, (
            f"'notify_solicitud_created' must NOT be enqueued for publisher/active vacancy. "
            f"Enqueued: {enqueued_tasks}"
        )
    finally:
        app.dependency_overrides.clear()
