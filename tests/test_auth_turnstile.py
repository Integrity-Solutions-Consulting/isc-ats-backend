import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.modules.auth.application.auth_service import (
    AuthService,
    TurnstileError,
)
from app.modules.auth.application.bootstrap_service import (
    ensure_candidate_role,
    grant_candidate_permissions_to_role,
    sync_permissions,
)
from app.modules.auth.application.turnstile import TurnstileOutcome
from app.modules.auth.infrastructure.models import Role, User
from app.modules.auth.infrastructure.repository import (
    RefreshTokenRepository,
    UserRepository,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository


class _FakeTurnstile:
    """Returns a fixed outcome and records the tokens it was asked to verify."""

    def __init__(self, outcome: TurnstileOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str | None, str | None]] = []

    async def verify(
        self, token: str | None, remote_ip: str | None
    ) -> TurnstileOutcome:
        self.calls.append((token, remote_ip))
        return self.outcome


def _service(session: AsyncSession, turnstile: _FakeTurnstile) -> AuthService:
    return AuthService(
        UserRepository(session),
        RefreshTokenRepository(session),
        ParameterRepository(session),
        turnstile=turnstile,
    )


async def _make_staff_user(
    session: AsyncSession, *, email: str, password: str = "secret123"
) -> User:
    portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "staff"
    )
    assert portal is not None
    user = User(
        email=email,
        password_hash=hash_password(password),
        portal_id=portal.id,
        email_verified=True,
    )
    return await UserRepository(session).add(user)


async def _bootstrap_candidate_role(session: AsyncSession) -> Role:
    await sync_permissions(session)
    role = await ensure_candidate_role(session)
    await grant_candidate_permissions_to_role(session, role.id)
    return role


# ---------------------------------------------------------------------------
# login — the token gate runs first; login FAILS OPEN when Cloudflare is down
# ---------------------------------------------------------------------------


async def test_login_rejects_failed_turnstile_before_credentials(
    session: AsyncSession,
) -> None:
    """A FAILED verdict blocks login even with the correct password, and the
    token is checked before any credential work."""
    await _make_staff_user(session, email="ts-fail@integrity.com.ec")
    fake = _FakeTurnstile(TurnstileOutcome.FAILED)

    with pytest.raises(TurnstileError):
        await _service(session, fake).login(
            "ts-fail@integrity.com.ec", "secret123", "1.2.3.4", turnstile_token="x"
        )
    assert fake.calls == [("x", "1.2.3.4")]


async def test_login_succeeds_when_turnstile_unavailable_fail_open(
    session: AsyncSession,
) -> None:
    """If Cloudflare can't be reached, login proceeds (fail-open) so an outage
    never locks out legitimate users."""
    await _make_staff_user(session, email="ts-open@integrity.com.ec")
    fake = _FakeTurnstile(TurnstileOutcome.UNAVAILABLE)

    tokens = await _service(session, fake).login(
        "ts-open@integrity.com.ec", "secret123", "1.2.3.4", turnstile_token="x"
    )
    assert tokens.access_token


async def test_login_succeeds_on_turnstile_success(session: AsyncSession) -> None:
    await _make_staff_user(session, email="ts-ok@integrity.com.ec")
    fake = _FakeTurnstile(TurnstileOutcome.SUCCESS)

    tokens = await _service(session, fake).login(
        "ts-ok@integrity.com.ec", "secret123", "1.2.3.4", turnstile_token="x"
    )
    assert tokens.access_token


# ---------------------------------------------------------------------------
# register — the abuse funnel; registration FAILS CLOSED when Cloudflare is down
# ---------------------------------------------------------------------------


async def test_register_rejects_failed_turnstile(session: AsyncSession) -> None:
    await _bootstrap_candidate_role(session)
    email = f"ts-{uuid.uuid4().hex[:10]}@test.local"
    fake = _FakeTurnstile(TurnstileOutcome.FAILED)

    with pytest.raises(TurnstileError):
        await _service(session, fake).register_candidate(
            email, "Pass1234!", "1.2.3.4", turnstile_token="x"
        )

    created = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    assert created is None, "no account may be created when the token fails"


async def test_register_fails_closed_when_turnstile_unavailable(
    session: AsyncSession,
) -> None:
    """Unlike login, registration is rejected when Cloudflare is unreachable —
    the funnel stays shut rather than open under an outage."""
    await _bootstrap_candidate_role(session)
    email = f"ts-{uuid.uuid4().hex[:10]}@test.local"
    fake = _FakeTurnstile(TurnstileOutcome.UNAVAILABLE)

    with pytest.raises(TurnstileError):
        await _service(session, fake).register_candidate(
            email, "Pass1234!", "1.2.3.4", turnstile_token="x"
        )


async def test_register_succeeds_on_turnstile_success(session: AsyncSession) -> None:
    await _bootstrap_candidate_role(session)
    email = f"ts-{uuid.uuid4().hex[:10]}@test.local"
    fake = _FakeTurnstile(TurnstileOutcome.SUCCESS)

    result = await _service(session, fake).register_candidate(
        email, "Pass1234!", "1.2.3.4", turnstile_token="x"
    )
    assert result.user.email == email
