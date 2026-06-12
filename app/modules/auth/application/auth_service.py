from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    verify_password,
    hash_password,
    create_verification_token,
)
from app.modules.auth.application.bootstrap_service import CANDIDATE_ROLE_NAME
from app.modules.auth.infrastructure.models import RefreshToken, Role, User, UserRole
from app.modules.auth.infrastructure.repository import (
    RefreshTokenRepository,
    UserRepository,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository


class AuthError(Exception):
    """Base authentication error."""


class InvalidCredentialsError(AuthError):
    pass


class EmailNotVerifiedError(AuthError):
    pass


class InvalidRefreshTokenError(AuthError):
    pass


class EmailAlreadyExistsError(AuthError):
    pass


class InvalidTokenError(AuthError):
    pass


@dataclass
class AuthTokens:
    """Login / refresh result. `portal` is the catalog CODE (hr | candidate)."""

    access_token: str
    refresh_token: str
    portal: str
    must_change_password: bool
    has_profile: bool = True


# Port: answers "does this user have a candidate profile?" without coupling
# auth to the recruitment module. Wired at the API layer (composition root).
ProfileChecker = Callable[[int], Awaitable[bool]]


class AuthService:
    """Authentication use cases. Branches on the portal CODE, never the id."""

    def __init__(
        self,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        parameters: ParameterRepository,
        has_profile_checker: ProfileChecker | None = None,
    ) -> None:
        self.users = users
        self.refresh_tokens = refresh_tokens
        self.parameters = parameters
        self.has_profile_checker = has_profile_checker

    async def login(self, email: str, password: str, ip: str | None) -> AuthTokens:
        user = await self.users.get_by_email(email)
        if user is None or user.password_hash is None:
            raise InvalidCredentialsError("Invalid email or password")
        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password")
        if not user.email_verified:
            raise EmailNotVerifiedError("Email is not verified")

        user.last_login_at = datetime.now(UTC)
        return await self._issue_tokens(user, ip)

    async def refresh(self, refresh_token: str, ip: str | None) -> AuthTokens:
        try:
            payload = decode_token(refresh_token)
        except jwt.PyJWTError as exc:
            raise InvalidRefreshTokenError("Invalid or expired refresh token") from exc
        if payload.get("type") != "refresh":
            raise InvalidRefreshTokenError("Not a refresh token")

        stored = await self.refresh_tokens.get_valid_by_hash(hash_token(refresh_token))
        if stored is None:
            raise InvalidRefreshTokenError("Refresh token revoked or unknown")

        user = await self.users.get(int(payload["sub"]))
        if user is None:
            raise InvalidRefreshTokenError("User no longer active")

        # Rotation: revoke the presented token, issue a fresh pair.
        await self.refresh_tokens.revoke(stored)
        return await self._issue_tokens(user, ip)

    async def logout(self, refresh_token: str) -> None:
        stored = await self.refresh_tokens.get_valid_by_hash(hash_token(refresh_token))
        if stored is not None:
            await self.refresh_tokens.revoke(stored)

    async def _resolve_portal_code(self, portal_id: int) -> str:
        portal = await self.parameters.get(portal_id)
        if portal is None:
            # Structural seed data must always exist; this signals a broken seed.
            raise AuthError("Portal parameter not found for user")
        return portal.code

    async def _issue_tokens(self, user: User, ip: str | None) -> AuthTokens:
        portal_code = await self._resolve_portal_code(user.portal_id)

        access = create_access_token(user.id, extra_claims={"portal": portal_code})
        refresh = create_refresh_token(user.id)

        expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
        await self.refresh_tokens.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_token(refresh),
                expires_at=expires_at,
                ip_address=ip,
                created_by=user.id,
                ip_created=ip,
            )
        )

        has_profile = True
        if portal_code == "candidate" and self.has_profile_checker is not None:
            has_profile = await self.has_profile_checker(user.id)

        return AuthTokens(
            access_token=access,
            refresh_token=refresh,
            portal=portal_code,
            must_change_password=user.must_change_password,
            has_profile=has_profile,
        )

    async def register_candidate(self, email: str, password: str, ip: str | None) -> User:
        existing_user = await self.users.get_by_email(email)
        if existing_user is not None:
            raise EmailAlreadyExistsError("El correo electrónico ya está registrado")

        portal = await self.parameters.get_by_type_and_code("user_portal", "candidate")
        if portal is None:
            raise AuthError("Portal de candidato no configurado")

        hashed = hash_password(password)
        new_user = User(
            email=email,
            password_hash=hashed,
            portal_id=portal.id,
            email_verified=True,  # Verified immediately!
            must_change_password=False,
            created_by=None,
            ip_created=ip,
        )
        new_user = await self.users.add(new_user)

        role_stmt = (
            select(Role)
            .where(Role.name == CANDIDATE_ROLE_NAME)
            .where(Role.is_active.is_(True))
        )
        role = (await self.users.session.execute(role_stmt)).scalar_one_or_none()
        if role is None:
            raise AuthError("Candidate role not configured — run bootstrap")
        user_role = UserRole(user_id=new_user.id, role_id=role.id)
        self.users.session.add(user_role)
        await self.users.session.flush()

        return new_user

    async def deactivate_user(self, user_id: int) -> None:
        """Set the user inactive and revoke all their active refresh tokens.

        Called by the self-deactivation route. Candidate-profile deactivation is
        the caller's responsibility (composition at the API layer).
        """
        user = await self.users.get(user_id, include_inactive=False)
        if user is not None:
            user.is_active = False
            await self.users.session.flush()
        await self.refresh_tokens.revoke_all_by_user_id(user_id)

    async def verify_email(self, token: str) -> None:
        try:
            payload = decode_token(token)
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("Token de verificación inválido o expirado") from exc

        if payload.get("type") != "verification":
            raise InvalidTokenError("Tipo de token no es de verificación")

        user_id = int(payload.get("sub", 0))
        user = await self.users.get(user_id)
        if user is None:
            raise InvalidTokenError("Usuario no encontrado")

        user.email_verified = True
        await self.users.session.flush()
