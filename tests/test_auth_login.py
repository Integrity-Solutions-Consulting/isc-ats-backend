import uuid

import jwt
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.modules.auth.application.auth_service import (
    AuthError,
    AuthService,
    EmailNotVerifiedError,
    InvalidCredentialsError,
)
from app.modules.auth.application.bootstrap_service import (
    CANDIDATE_ROLE_NAME,
    bootstrap_admin,
    sync_permissions,
    ensure_candidate_role,
    grant_candidate_permissions_to_role,
)
from app.modules.auth.infrastructure.models import Role, User, UserRole
from app.modules.auth.infrastructure.repository import (
    RefreshTokenRepository,
    UserRepository,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository


def _service(session: AsyncSession) -> AuthService:
    return AuthService(
        UserRepository(session),
        RefreshTokenRepository(session),
        ParameterRepository(session),
    )


async def _make_staff_user(
    session: AsyncSession,
    *,
    email: str = "smoke@integrity.com.ec",
    password: str = "secret123",
    verified: bool = True,
) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code("user_portal", "staff")
    assert portal is not None, "user_portal:staff must be seeded (run alembic upgrade head)"
    user = User(
        email=email,
        password_hash=hash_password(password),
        portal_id=portal.id,
        email_verified=verified,
    )
    return await UserRepository(session).add(user)


async def test_login_resolves_portal_by_code(session: AsyncSession) -> None:
    await _make_staff_user(session)

    tokens = await _service(session).login("smoke@integrity.com.ec", "secret123", "127.0.0.1")

    assert tokens.portal == "staff"
    assert tokens.access_token and tokens.refresh_token
    claims = jwt.decode(
        tokens.access_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
    assert claims["portal"] == "staff"
    assert claims["type"] == "access"


async def test_login_wrong_password_rejected(session: AsyncSession) -> None:
    await _make_staff_user(session)
    with pytest.raises(InvalidCredentialsError):
        await _service(session).login("smoke@integrity.com.ec", "wrong", "127.0.0.1")


async def test_login_unverified_email_rejected(session: AsyncSession) -> None:
    await _make_staff_user(session, email="unverified@integrity.com.ec", verified=False)
    with pytest.raises(EmailNotVerifiedError):
        await _service(session).login("unverified@integrity.com.ec", "secret123", "127.0.0.1")


# ---------------------------------------------------------------------------
# register_candidate — role assignment and fail-loud behaviour
# ---------------------------------------------------------------------------


async def _bootstrap_candidate_role(session: AsyncSession) -> Role:
    """Minimal setup: sync permissions + create the candidate role with grants."""
    await sync_permissions(session)
    role = await ensure_candidate_role(session)
    await grant_candidate_permissions_to_role(session, role.id)
    return role


async def test_register_candidate_assigns_candidate_role(session: AsyncSession) -> None:
    """A newly registered candidate must be assigned the candidate role."""
    await _bootstrap_candidate_role(session)
    email = f"cand-{uuid.uuid4().hex[:10]}@test.local"

    user = await _service(session).register_candidate(email, "Pass1234!", "127.0.0.1")

    assigned = (
        await session.execute(
            select(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user.id)
            .where(Role.name == CANDIDATE_ROLE_NAME)
        )
    ).scalar_one_or_none()
    assert assigned is not None, "candidate role must be assigned on registration"


async def test_register_candidate_starts_unverified(session: AsyncSession) -> None:
    """Registration must leave the account unverified until the email link is clicked."""
    await _bootstrap_candidate_role(session)
    email = f"cand-{uuid.uuid4().hex[:10]}@test.local"

    user = await _service(session).register_candidate(email, "Pass1234!", "127.0.0.1")

    assert user.email_verified is False


async def test_register_candidate_raises_when_role_is_missing(session: AsyncSession) -> None:
    """If the candidate role was never bootstrapped, register_candidate must raise AuthError."""
    # The shared dev database may already hold a bootstrapped candidate role;
    # deactivate it inside this rolled-back transaction to simulate its absence.
    await session.execute(
        update(Role).where(Role.name == CANDIDATE_ROLE_NAME).values(is_active=False)
    )
    email = f"cand-{uuid.uuid4().hex[:10]}@test.local"

    with pytest.raises(AuthError, match="[Cc]andidate role"):
        await _service(session).register_candidate(email, "Pass1234!", "127.0.0.1")
