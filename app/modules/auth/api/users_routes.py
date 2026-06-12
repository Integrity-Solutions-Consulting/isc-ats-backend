from __future__ import annotations

from typing import Annotated
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select

from app.core.dependencies import CurrentUserDep, SessionDep
from app.core.security import hash_password
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import Role, User, UserRole
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.pagination import Page, PageParams


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    portal_id: int
    is_active: bool
    email_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    roles: list[str] = []


class UserStatusUpdate(BaseModel):
    is_active: bool


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)
    role_id: int
    is_active: bool = True


router = APIRouter(prefix="/users", tags=["auth · users"])


async def _fetch_roles_by_user_ids(
    session, user_ids: list[int]
) -> dict[int, list[str]]:
    """Return a mapping of user_id -> list of active role names.

    Uses a single JOIN query to avoid N+1.
    """
    if not user_ids:
        return {}

    stmt = (
        select(UserRole.user_id, Role.name)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(user_ids))
        .where(UserRole.is_active.is_(True))
        .where(Role.is_active.is_(True))
    )
    rows = (await session.execute(stmt)).all()

    result: dict[int, list[str]] = {uid: [] for uid in user_ids}
    for user_id, role_name in rows:
        result[user_id].append(role_name)
    return result


@router.get(
    "",
    response_model=Page[UserRead],
    dependencies=[Depends(require_permission("auth.users.read"))],
)
async def list_users(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[UserRead]:
    repo = UserRepository(session)
    params = PageParams(page=page, size=size)
    # include_inactive=True so admins can see and reactivate deactivated users
    items, total = await repo.list(params, include_inactive=True)

    user_ids = [u.id for u in items]
    roles_map = await _fetch_roles_by_user_ids(session, user_ids)

    user_reads = []
    for u in items:
        ur = UserRead.model_validate(u)
        ur.roles = roles_map.get(u.id, [])
        user_reads.append(ur)

    return Page.create(user_reads, total, params)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("auth.users.create"))],
)
async def create_user(
    data: UserCreate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> UserRead:
    """Create a staff-portal user with an initial password and an assigned role.

    The admin provides the initial password directly (no email infrastructure).
    The user can log in immediately with the given credentials.
    """
    # Ensure the target role exists and is active.
    role = (
        await session.execute(
            select(Role).where(Role.id == data.role_id).where(Role.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role {data.role_id} not found or inactive",
        )

    # Check email uniqueness.
    existing = (
        await session.execute(select(User).where(User.email == data.email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with email '{data.email}' already exists",
        )

    # Resolve the staff portal id.
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    if portal is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="user_portal:staff parameter not found — run bootstrap first",
        )

    repo = UserRepository(session)
    user = await repo.add(
        User(
            email=data.email,
            password_hash=hash_password(data.password),
            portal_id=portal.id,
            email_verified=True,
            is_active=data.is_active,
            must_change_password=True,
            created_by=current_user.user_id,
            ip_created=current_user.ip,
        )
    )

    # Assign the requested role.
    session.add(UserRole(user_id=user.id, role_id=role.id, created_by=current_user.user_id))
    await session.flush()

    ur = UserRead.model_validate(user)
    ur.roles = [role.name]
    return ur


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(require_permission("auth.users.update"))],
)
async def update_user_status(
    user_id: int,
    data: UserStatusUpdate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> UserRead:
    # Self-deactivation guard
    if not data.is_active and user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account (self-deactivation not allowed)",
        )

    # Fetch user regardless of is_active status so we can reactivate
    stmt = select(User).where(User.id == user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found")

    repo = UserRepository(session)
    changes: dict = {
        "is_active": data.is_active,
        "updated_by": current_user.user_id,
        "ip_updated": current_user.ip,
    }
    user = await repo.update(user, changes)

    roles_map = await _fetch_roles_by_user_ids(session, [user.id])
    ur = UserRead.model_validate(user)
    ur.roles = roles_map.get(user.id, [])
    return ur
