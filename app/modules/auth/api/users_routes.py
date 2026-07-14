from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, select

from app.core.config import settings
from app.core.dependencies import CurrentUserDep, SessionDep
from app.core.task_queue import TaskQueueDep, register_task
from app.core.security import hash_password
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import Role, User, UserRole
from app.modules.auth.infrastructure.repository import (
    RefreshTokenRepository,
    UserRepository,
)
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
    password: str | None = Field(default=None, max_length=72)
    role_id: int
    is_active: bool = True

    @field_validator("password")
    @classmethod
    def _enforce_password_policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from app.shared.validators import password_policy_error

        error = password_policy_error(value)
        if error:
            raise ValueError(error)
        return value


router = APIRouter(prefix="/users", tags=["auth · users"])


def _generate_random_password() -> str:
    """A secure random password satisfying the policy: min 12 chars, 3 each of
    lowercase/uppercase/digit/special."""
    import secrets
    import string

    lower = "".join(secrets.choice(string.ascii_lowercase) for _ in range(3))
    upper = "".join(secrets.choice(string.ascii_uppercase) for _ in range(3))
    digits = "".join(secrets.choice(string.digits) for _ in range(3))
    special = "".join(secrets.choice("!@#$%^&*()-_=+") for _ in range(3))
    all_chars = list(lower + upper + digits + special)
    secrets.SystemRandom().shuffle(all_chars)
    return "".join(all_chars)


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
    task_queue: TaskQueueDep,
) -> UserRead:
    """Create a staff-portal user with an assigned role.
    
    If the password is not provided, a random secure password will be generated
    and sent to the user via email.
    """
    password_raw = data.password
    if password_raw is None:
        password_raw = _generate_random_password()

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

    # Check email uniqueness. Case-insensitive and whitespace-tolerant so
    # "Foo@x.com" cannot slip past a lowercase-stored "foo@x.com" (mirrors
    # UserRepository.get_by_email normalization).
    normalized_email = data.email.strip().lower()
    existing = (
        await session.execute(
            select(User).where(func.lower(User.email) == normalized_email)
        )
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
            password_hash=hash_password(password_raw),
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
    
    if data.password is None:
        # Must be awaited — enqueue is a coroutine; a bare call creates it but
        # never runs it, silently dropping the temp-password email.
        await task_queue.enqueue("send_random_password_email", user.email, password_raw)

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
    request: Request,
    task_queue: TaskQueueDep,
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

    # A reactivation resends the welcome email ONLY when this user never made it
    # past their original invite (never logged in, still on the must-change
    # temp password) — e.g. created, the welcome email never arrived, then
    # deactivated instead of deleted. Reactivating someone who already has real
    # credentials (was deactivated for an unrelated reason, e.g. leave) must
    # NOT overwrite their password or blast them a "temp password" email.
    resend_welcome_email = (
        data.is_active
        and not user.is_active
        and user.must_change_password
        and user.last_login_at is None
    )
    new_password_raw = _generate_random_password() if resend_welcome_email else None

    repo = UserRepository(session)
    changes: dict = {
        "is_active": data.is_active,
        "updated_by": current_user.user_id,
        "ip_updated": current_user.ip,
    }
    if new_password_raw is not None:
        changes["password_hash"] = hash_password(new_password_raw)
    user = await repo.update(user, changes)

    # Deactivation must end all live sessions, not just flip the flag — otherwise
    # a deactivated user keeps working until their tokens self-expire. Mirror
    # AuthService.deactivate_user / change_password: revoke every refresh token
    # and mark all access tokens issued before now as revoked.
    if not data.is_active:
        await RefreshTokenRepository(session).revoke_all_by_user_id(user_id)
        token_denylist = request.app.state.token_denylist
        if token_denylist is not None:
            ttl = settings.access_token_expire_minutes * 60 + 60  # + clock-skew buffer
            await token_denylist.revoke_user(user_id, ttl)

    if new_password_raw is not None:
        # Must be awaited — see create_user's identical note on enqueue.
        await task_queue.enqueue("send_random_password_email", user.email, new_password_raw)

    roles_map = await _fetch_roles_by_user_ids(session, [user.id])
    ur = UserRead.model_validate(user)
    ur.roles = roles_map.get(user.id, [])
    return ur


async def _send_random_password_email(to_email: str, password_raw: str) -> None:
    from app.core.database import async_session_factory
    from app.modules.comms.application.email_dispatch_service import EmailDispatchService
    from app.modules.comms.application.email_templates import render_random_password_email
    from app.modules.comms.application.email_sender import EmailMessage
    from app.modules.comms.infrastructure.email_sender_factory import build_email_sender
    import logging

    logger = logging.getLogger(__name__)
    rendered = render_random_password_email(to_email, password_raw)
    message = EmailMessage(
        to_email=to_email,
        subject=rendered.subject,
        html_body=rendered.html_body,
        text_body=rendered.text_body,
    )
    async with async_session_factory() as session:
        try:
            dispatch = EmailDispatchService(session, build_email_sender())
            success = await dispatch.send(message)
            await session.commit()
            if not success:
                logger.error(
                    "Random password email delivery failed for %s",
                    to_email,
                )
        except Exception:
            logger.exception("Unexpected error sending random password email to %s", to_email)
            await session.rollback()

register_task("send_random_password_email", _send_random_password_email)
