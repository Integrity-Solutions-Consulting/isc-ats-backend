"""Slice 6 — Profile template copy (spec: profile templates copy).

Tasks:
  6.1  ProfileTemplateService.copy(...) returns a NEW template plus its items
       deep-copied — not shared references, not the same DB rows as the source.
  6.2  (GREEN for 6.1 — implementation lives in profile_templates_service.py)
  6.3  POST /profile-templates/{id}/copy route: 403 when caller lacks
       org.profile_templates.create; 201 with a new id when caller has it.
  6.4  (GREEN for 6.3 — route wired with require_permission)

All async tests use a rolled-back session (unit-level) or the full ASGI app
(route-level).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.application.profile_templates_service import ProfileTemplateService
from app.modules.org.infrastructure.models import ProfileTemplate, ProfileTemplateItem
from app.shared.repository import BaseRepository


def _service(session: AsyncSession) -> ProfileTemplateService:
    return ProfileTemplateService(
        BaseRepository(session, ProfileTemplate),
        items_repository=BaseRepository(session, ProfileTemplateItem),
    )


def _actor() -> object:
    """Minimal CurrentUser stand-in — service only reads .user_id / .ip."""
    from app.core.dependencies import CurrentUser  # noqa: PLC0415

    return CurrentUser(user_id=1, ip="127.0.0.1")


async def _make_category(session: AsyncSession) -> int:
    """A parameter of type 'template_item_category', required by item FKs."""
    from app.modules.org.infrastructure.models import Parameter  # noqa: PLC0415

    param = await BaseRepository(session, Parameter).add(
        Parameter(
            type="template_item_category",
            code=f"cat-{uuid.uuid4().hex[:8]}",
            name="Skills",
        )
    )
    return param.id


# ---------------------------------------------------------------------------
# 6.1 / 6.2 — ProfileTemplateService.copy
# ---------------------------------------------------------------------------


async def test_copy_creates_a_new_template_with_a_different_id(
    session: AsyncSession,
) -> None:
    source = await BaseRepository(session, ProfileTemplate).add(
        ProfileTemplate(name="Backend .NET Senior")
    )
    service = _service(session)

    copy = await service.copy(source.id, _actor())

    assert copy.id != source.id
    assert copy.name != ""


async def test_copy_deep_copies_items_as_new_rows_not_shared_references(
    session: AsyncSession,
) -> None:
    source = await BaseRepository(session, ProfileTemplate).add(
        ProfileTemplate(name="Backend .NET Senior")
    )
    category_id = await _make_category(session)
    item_repo = BaseRepository(session, ProfileTemplateItem)
    source_item = await item_repo.add(
        ProfileTemplateItem(template_id=source.id, category_id=category_id, name="C#")
    )
    service = _service(session)

    copy = await service.copy(source.id, _actor())

    stmt = select(ProfileTemplateItem).where(ProfileTemplateItem.template_id == copy.id)
    copied_items = (await session.execute(stmt)).scalars().all()

    assert len(copied_items) == 1
    copied_item = copied_items[0]
    assert copied_item.id != source_item.id
    assert copied_item.template_id != source_item.template_id
    assert copied_item.name == source_item.name
    assert copied_item.category_id == source_item.category_id

    # Mutating the copy must never affect the source (proves no shared reference).
    copied_item.name = "Angular"
    await session.flush()
    refreshed_source_item = await item_repo.get(source_item.id)
    assert refreshed_source_item is not None
    assert refreshed_source_item.name == "C#"


async def test_copy_with_no_items_produces_an_empty_item_set(
    session: AsyncSession,
) -> None:
    source = await BaseRepository(session, ProfileTemplate).add(
        ProfileTemplate(name="Empty Template")
    )
    service = _service(session)

    copy = await service.copy(source.id, _actor())

    stmt = select(ProfileTemplateItem).where(ProfileTemplateItem.template_id == copy.id)
    copied_items = (await session.execute(stmt)).scalars().all()
    assert copied_items == []


# ---------------------------------------------------------------------------
# Route-level helpers
# ---------------------------------------------------------------------------


async def _staff_user_with_role(session: AsyncSession, role_name: str, *, tag: str) -> object:
    """Bootstrap the RBAC baseline and return a fresh User in the given internal role."""
    from app.modules.auth.application.bootstrap_service import bootstrap_admin  # noqa: PLC0415
    from app.modules.auth.infrastructure.models import Role, User, UserRole  # noqa: PLC0415
    from app.modules.org.infrastructure.parameters_repository import (  # noqa: PLC0415
        ParameterRepository,
    )

    admin_result = await bootstrap_admin(
        session, f"admin-{uuid.uuid4().hex[:8]}@test.local", "S3cret"
    )

    params_repo = ParameterRepository(session)
    staff_portal = await params_repo.get_by_type_and_code("user_portal", "staff")
    assert staff_portal is not None

    role = (
        await session.execute(
            select(Role).where(Role.name == role_name).where(Role.is_active.is_(True))
        )
    ).scalar_one()

    user = User(
        email=f"{role_name.lower().replace(' ', '')}-{tag}@test.local",
        portal_id=staff_portal.id,
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_id=role.id, is_active=True))
    await session.flush()
    return user, admin_result


# ---------------------------------------------------------------------------
# 6.3 / 6.4 — POST /profile-templates/{id}/copy route
# ---------------------------------------------------------------------------


async def test_copy_route_forbids_caller_without_create_permission(
    session: AsyncSession,
) -> None:
    """Talento Humano (profile_templates.read only) copying a template → 403."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Talento Humano", tag=tag)
    template = await BaseRepository(session, ProfileTemplate).add(
        ProfileTemplate(name="Backend .NET Senior")
    )

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/org/profile-templates/{template.id}/copy",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


async def test_copy_route_allows_caller_with_create_permission(
    session: AsyncSession,
) -> None:
    """Comercial (has profile_templates.create) copying a template → 201, new id."""
    from app.core.database import get_session  # noqa: PLC0415
    from app.core.security import create_access_token  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    tag = uuid.uuid4().hex[:8]
    user, _admin = await _staff_user_with_role(session, "Comercial", tag=tag)
    template = await BaseRepository(session, ProfileTemplate).add(
        ProfileTemplate(name="Backend .NET Senior")
    )

    async def _use_test_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    try:
        token = create_access_token(user.id, extra_claims={"portal": "staff"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                f"/api/v1/org/profile-templates/{template.id}/copy",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["id"] != template.id
    finally:
        app.dependency_overrides.clear()
