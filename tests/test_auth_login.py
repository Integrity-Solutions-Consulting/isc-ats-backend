import uuid

import jwt
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.login_throttle import MAX_FAILED_ATTEMPTS, InMemoryLoginThrottle
from app.core.security import hash_password
from app.modules.auth.application.auth_service import (
    AccountLockedError,
    AuthError,
    AuthService,
    EmailNotVerifiedError,
    InvalidCredentialsError,
)
from app.modules.auth.application.bootstrap_service import (
    CANDIDATE_ROLE_NAME,
    ensure_candidate_role,
    grant_candidate_permissions_to_role,
    sync_permissions,
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


def _service_with_throttle(
    session: AsyncSession, throttle: InMemoryLoginThrottle
) -> AuthService:
    return AuthService(
        UserRepository(session),
        RefreshTokenRepository(session),
        ParameterRepository(session),
        login_throttle=throttle,
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


async def test_login_is_case_insensitive_for_email(session: AsyncSession) -> None:
    """A stored lowercase email must authenticate even when the client sends it
    capitalized. Mobile keyboards and some browsers auto-capitalize the first
    letter of the email field, which was silently causing a 401 on the correct
    password."""
    await _make_staff_user(session, email="mixedcase@integrity.com.ec")

    tokens = await _service(session).login(
        "MixedCase@Integrity.com.ec", "secret123", "127.0.0.1"
    )

    assert tokens.access_token and tokens.refresh_token


async def test_login_ignores_surrounding_whitespace_in_email(session: AsyncSession) -> None:
    """A trailing space introduced by copy-paste or autofill must not block login."""
    await _make_staff_user(session, email="spaced@integrity.com.ec")

    tokens = await _service(session).login(
        "  spaced@integrity.com.ec  ", "secret123", "127.0.0.1"
    )

    assert tokens.access_token


async def test_login_wrong_password_rejected(session: AsyncSession) -> None:
    await _make_staff_user(session)
    with pytest.raises(InvalidCredentialsError):
        await _service(session).login("smoke@integrity.com.ec", "wrong", "127.0.0.1")


async def test_login_unverified_email_rejected(session: AsyncSession) -> None:
    await _make_staff_user(session, email="unverified@integrity.com.ec", verified=False)
    with pytest.raises(EmailNotVerifiedError):
        await _service(session).login("unverified@integrity.com.ec", "secret123", "127.0.0.1")


# ---------------------------------------------------------------------------
# login throttle — per-account brute-force lockout
# ---------------------------------------------------------------------------


async def test_login_locks_account_after_repeated_failures(session: AsyncSession) -> None:
    """After MAX_FAILED_ATTEMPTS wrong passwords, even the correct password is
    refused with AccountLockedError until the lock expires."""
    await _make_staff_user(session, email="brute@integrity.com.ec")
    throttle = InMemoryLoginThrottle()
    service = _service_with_throttle(session, throttle)

    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            await service.login("brute@integrity.com.ec", "wrong", "127.0.0.1")

    # The account is now locked: the right password is rejected too.
    with pytest.raises(AccountLockedError) as exc_info:
        await service.login("brute@integrity.com.ec", "secret123", "127.0.0.1")
    assert exc_info.value.retry_after > 0


async def test_successful_login_resets_failure_counter(session: AsyncSession) -> None:
    """A successful login clears prior failures, so a user who mistypes then logs
    in correctly never gets locked by later isolated typos."""
    await _make_staff_user(session, email="reset@integrity.com.ec")
    throttle = InMemoryLoginThrottle()
    service = _service_with_throttle(session, throttle)

    # One short of the threshold...
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(InvalidCredentialsError):
            await service.login("reset@integrity.com.ec", "wrong", "127.0.0.1")

    # ...then a correct login resets the counter.
    tokens = await service.login("reset@integrity.com.ec", "secret123", "127.0.0.1")
    assert tokens.access_token

    # A fresh failure must not immediately re-lock (counter started over).
    with pytest.raises(InvalidCredentialsError):
        await service.login("reset@integrity.com.ec", "wrong", "127.0.0.1")
    assert await throttle.locked_for("reset@integrity.com.ec") is None


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

    result = await _service(session).register_candidate(email, "Pass1234!", "127.0.0.1")

    assert result.reactivation is False
    assigned = (
        await session.execute(
            select(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == result.user.id)
            .where(Role.name == CANDIDATE_ROLE_NAME)
        )
    ).scalar_one_or_none()
    assert assigned is not None, "candidate role must be assigned on registration"


async def test_register_candidate_starts_unverified(session: AsyncSession) -> None:
    """Registration must leave the account unverified until the email link is clicked."""
    await _bootstrap_candidate_role(session)
    email = f"cand-{uuid.uuid4().hex[:10]}@test.local"

    result = await _service(session).register_candidate(email, "Pass1234!", "127.0.0.1")

    assert result.user.email_verified is False


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


async def test_register_candidate_reactivates_inactive_candidate(
    session: AsyncSession,
) -> None:
    """Re-registering an INACTIVE candidate's email is a reactivation, not a block.

    The password is refreshed and the caller is told to send a reactivation email,
    but the account stays OFF (is_active False) until the email link is clicked —
    only then is ownership proven. No second row is ever created.
    """
    await _bootstrap_candidate_role(session)
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None
    email = f"inactive-{uuid.uuid4().hex[:10]}@test.local"
    old_hash = hash_password("OldPass1234!")
    inactive = User(
        email=email,
        password_hash=old_hash,
        portal_id=portal.id,
        email_verified=True,
        is_active=False,
    )
    await UserRepository(session).add(inactive)

    result = await _service(session).register_candidate(email, "NewPass1234!", "127.0.0.1")

    assert result.reactivation is True
    assert result.user.id == inactive.id, "must reuse the same row, not create a new one"
    assert result.user.is_active is False, "account stays off until the email link is clicked"
    assert result.user.password_hash != old_hash, "password must be refreshed on reactivation"


async def test_register_candidate_rejects_email_of_active_user(
    session: AsyncSession,
) -> None:
    """An email held by an ACTIVE account is a real conflict and must be rejected."""
    from app.modules.auth.application.auth_service import EmailAlreadyExistsError

    await _bootstrap_candidate_role(session)
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    assert portal is not None
    email = f"active-{uuid.uuid4().hex[:10]}@test.local"
    active = User(
        email=email,
        password_hash=hash_password("Pass1234!"),
        portal_id=portal.id,
        email_verified=True,
        is_active=True,
    )
    await UserRepository(session).add(active)

    with pytest.raises(EmailAlreadyExistsError):
        await _service(session).register_candidate(email, "Pass1234!", "127.0.0.1")


# ---------------------------------------------------------------------------
# reset_password — lifts the login throttle lock
# ---------------------------------------------------------------------------


async def test_reset_password_lifts_login_lock(session: AsyncSession) -> None:
    """A successful password reset must clear the login throttle lock, so a user
    who was locked out (and therefore reset their password) can log in again."""
    from app.core.security import create_password_reset_token

    user = await _make_staff_user(session, email="locked-reset@integrity.com.ec")
    throttle = InMemoryLoginThrottle()
    service = _service_with_throttle(session, throttle)

    # Trip the lock.
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            await service.login("locked-reset@integrity.com.ec", "wrong", "127.0.0.1")
    assert await throttle.locked_for("locked-reset@integrity.com.ec") is not None

    assert user.password_hash is not None
    token = create_password_reset_token(user.id, user.password_hash)
    await service.reset_password(token, "BrandNewPass9!")

    # The lock is gone; a correct login succeeds immediately.
    assert await throttle.locked_for("locked-reset@integrity.com.ec") is None
    tokens = await service.login(
        "locked-reset@integrity.com.ec", "BrandNewPass9!", "127.0.0.1"
    )
    assert tokens.access_token


# ---------------------------------------------------------------------------
# refresh — reuse detection revokes the token family
# ---------------------------------------------------------------------------


async def test_refresh_reuse_revokes_family(session: AsyncSession) -> None:
    """Presenting a valid-but-already-rotated refresh token (reuse) must revoke
    every refresh token for the user, not just reject the request."""
    from datetime import UTC, datetime

    from app.core.token_denylist import InMemoryTokenDenylist
    from app.modules.auth.application.auth_service import InvalidRefreshTokenError
    from app.modules.auth.infrastructure.models import RefreshToken

    user = await _make_staff_user(session, email="reuse@integrity.com.ec")
    denylist = InMemoryTokenDenylist()
    service = AuthService(
        UserRepository(session),
        RefreshTokenRepository(session),
        ParameterRepository(session),
        token_denylist=denylist,
    )

    # A first login issues a valid refresh token pair.
    tokens = await service.login("reuse@integrity.com.ec", "secret123", "127.0.0.1")

    # Rotate it once (the presented token is now revoked in storage).
    rotated = await service.refresh(tokens.refresh_token, "127.0.0.1")
    assert rotated.refresh_token

    # Replaying the ORIGINAL (now-rotated) token is reuse: reject AND revoke the
    # whole family, including the freshly rotated token.
    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh(tokens.refresh_token, "127.0.0.1")

    # Every refresh token for this user must now be revoked.
    active = list(
        (
            await session.execute(
                select(RefreshToken)
                .where(RefreshToken.user_id == user.id)
                .where(RefreshToken.revoked_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    assert active == []
    # And the access-token denylist marker is set for the user.
    assert await denylist.is_user_revoked(user.id, int(datetime.now(UTC).timestamp())) is True
