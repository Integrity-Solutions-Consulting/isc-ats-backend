from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import func, select

from app.core.config import settings
from app.core.login_throttle import LoginThrottle
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    password_fingerprint,
    verify_password,
)
from app.core.token_denylist import TokenDenylist
from app.modules.auth.application.bootstrap_service import CANDIDATE_ROLE_NAME
from app.modules.auth.application.turnstile import TurnstileOutcome, TurnstileVerifier
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


class SamePasswordError(AuthError):
    pass


class TurnstileError(AuthError):
    """The anti-bot (Turnstile) check did not pass.

    Raised for a FAILED verdict on either flow, and additionally for an
    UNAVAILABLE verdict on registration (fail-closed). Login treats UNAVAILABLE
    as a pass (fail-open) and never raises this for an outage.
    """


@dataclass
class RegistrationResult:
    """Outcome of a candidate registration attempt.

    `reactivation` is True when the email belonged to a deactivated candidate who
    is coming back: the caller sends a reactivation email instead of a first-time
    verification email. The account stays off until the link is clicked.
    """

    user: "User"
    reactivation: bool


class AccountLockedError(AuthError):
    """Too many failed logins for this account; temporarily locked.

    Carries the seconds the caller should wait before retrying, so the API can
    surface a Retry-After header.
    """

    def __init__(self, retry_after: int) -> None:
        super().__init__("Cuenta temporalmente bloqueada por demasiados intentos")
        self.retry_after = retry_after


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
        token_denylist: TokenDenylist | None = None,
        login_throttle: LoginThrottle | None = None,
        turnstile: TurnstileVerifier | None = None,
    ) -> None:
        self.users = users
        self.refresh_tokens = refresh_tokens
        self.parameters = parameters
        self.has_profile_checker = has_profile_checker
        self.token_denylist = token_denylist
        self.login_throttle = login_throttle
        self.turnstile = turnstile

    async def _check_turnstile(
        self, token: str | None, ip: str | None, *, fail_closed: bool
    ) -> None:
        """Run the anti-bot gate, applying the flow's fail policy.

        No verifier wired (feature off) → skip. FAILED always blocks. UNAVAILABLE
        blocks only when `fail_closed` (registration); login passes it (fail-open)
        so a Cloudflare outage never locks out legitimate users.
        """
        if self.turnstile is None:
            return
        outcome = await self.turnstile.verify(token, ip)
        if outcome is TurnstileOutcome.SUCCESS:
            return
        if outcome is TurnstileOutcome.FAILED:
            raise TurnstileError(
                "No pudimos verificar que no seas un robot. "
                "Recargá la página e intentá de nuevo."
            )
        # UNAVAILABLE
        if fail_closed:
            raise TurnstileError(
                "No pudimos completar la verificación de seguridad. "
                "Intentá nuevamente en unos minutos."
            )

    async def _revoke_access_tokens(self, user_id: int) -> None:
        """Kill every access token the user currently holds (security fix 3.4).

        Paired with refresh-token revocation: a password change or deactivation
        must end ALL live sessions, not just the refresh side. No-op when no
        denylist is wired (the access token then self-expires within its TTL).
        """
        if self.token_denylist is not None:
            ttl = settings.access_token_expire_minutes * 60 + 60  # + clock-skew buffer
            await self.token_denylist.revoke_user(user_id, ttl)

    async def login(
        self,
        email: str,
        password: str,
        ip: str | None,
        turnstile_token: str | None = None,
    ) -> AuthTokens:
        # Anti-bot gate first — before any DB work or throttle bookkeeping. Login
        # fails OPEN if Cloudflare is unreachable so an outage can't lock out real
        # users; only a FAILED verdict blocks here.
        await self._check_turnstile(turnstile_token, ip, fail_closed=False)

        # Account-level brute-force guard: refuse before touching the DB while the
        # account is locked. Checked for any email (existing or not) so the
        # behaviour can't be used to probe which emails are registered.
        if self.login_throttle is not None:
            locked = await self.login_throttle.locked_for(email)
            if locked is not None:
                raise AccountLockedError(locked)

        user = await self.users.get_by_email(email)
        if user is None or user.password_hash is None:
            await self._record_login_failure(email)
            raise InvalidCredentialsError("Invalid email or password")
        if not verify_password(password, user.password_hash):
            await self._record_login_failure(email)
            raise InvalidCredentialsError("Invalid email or password")
        if not user.email_verified:
            # Correct credentials, just unverified — not a brute-force signal, so
            # it neither counts as a failure nor resets the counter.
            raise EmailNotVerifiedError("Email is not verified")

        if self.login_throttle is not None:
            await self.login_throttle.reset(email)
        user.last_login_at = datetime.now(UTC)
        return await self._issue_tokens(user, ip)

    async def _record_login_failure(self, email: str) -> None:
        if self.login_throttle is not None:
            await self.login_throttle.record_failure(email)

    async def refresh(self, refresh_token: str, ip: str | None) -> AuthTokens:
        try:
            payload = decode_token(refresh_token)
        except jwt.PyJWTError as exc:
            raise InvalidRefreshTokenError("Invalid or expired refresh token") from exc
        if payload.get("type") != "refresh":
            raise InvalidRefreshTokenError("Not a refresh token")

        stored = await self.refresh_tokens.get_valid_by_hash(hash_token(refresh_token))
        if stored is None:
            # Reuse detection: the JWT is valid and unexpired, yet no active stored
            # token matches its hash. That means this token was already rotated
            # away (revoked) and is being replayed — a classic refresh-token theft
            # signal. Kill the whole family (all refresh + access tokens) so a
            # stolen-but-rotated token can't be used to bootstrap a new session.
            sub = payload.get("sub")
            if sub is not None:
                user_id = int(sub)
                await self.refresh_tokens.revoke_all_by_user_id(user_id)
                await self._revoke_access_tokens(user_id)
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

    async def register_candidate(
        self,
        email: str,
        password: str,
        ip: str | None,
        turnstile_token: str | None = None,
    ) -> RegistrationResult:
        # Anti-bot gate first — registration is the abuse funnel, so it fails
        # CLOSED: a FAILED verdict OR an unreachable Cloudflare both block.
        await self._check_turnstile(turnstile_token, ip, fail_closed=True)

        # Look up by normalized email across ALL rows (get_by_email filters
        # is_active==True and would miss a deactivated account). An active row is a
        # real conflict; an inactive CANDIDATE row is a returning user, handled as
        # a reactivation instead of a hard block.
        normalized_email = email.strip().lower()
        existing_stmt = select(User).where(func.lower(User.email) == normalized_email)
        existing_user = (
            await self.users.session.execute(existing_stmt)
        ).scalar_one_or_none()

        portal = await self.parameters.get_by_type_and_code("user_portal", "candidate")
        if portal is None:
            raise AuthError("Portal de candidato no configurado")

        if existing_user is not None:
            if not existing_user.is_active and existing_user.portal_id == portal.id:
                # Returning candidate: refresh the password now, but keep the
                # account off until the reactivation link proves email ownership
                # (see verify_email). Prevents takeover of an abandoned account.
                existing_user.password_hash = hash_password(password)
                await self.users.session.flush()
                return RegistrationResult(user=existing_user, reactivation=True)
            raise EmailAlreadyExistsError("El correo electrónico ya está registrado")

        hashed = hash_password(password)
        new_user = User(
            email=email,
            password_hash=hashed,
            portal_id=portal.id,
            # Activated only after the candidate clicks the verification link sent
            # by email; login rejects unverified accounts (EmailNotVerifiedError).
            email_verified=False,
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

        return RegistrationResult(user=new_user, reactivation=False)

    async def get_unverified_user(self, email: str) -> User | None:
        """Return the user for `email` only if it exists and is not yet verified.

        Used by the resend-verification flow. Returns None otherwise so the caller
        can answer generically without revealing whether the email is registered.
        """
        user = await self.users.get_by_email(email)
        if user is None or user.email_verified:
            return None
        return user

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
        await self._revoke_access_tokens(user_id)

    async def change_password(
        self, user_id: int, current_password: str, new_password: str
    ) -> None:
        """Change an authenticated user's password.

        Verifies the current password, rejects a no-op change, re-hashes the new
        one, clears the must_change_password flag, and revokes all refresh tokens
        so any other active session is forced to re-authenticate.
        """
        user = await self.users.get(user_id)
        if user is None or user.password_hash is None:
            raise InvalidCredentialsError("La contraseña actual es incorrecta")
        if not verify_password(current_password, user.password_hash):
            raise InvalidCredentialsError("La contraseña actual es incorrecta")
        if verify_password(new_password, user.password_hash):
            raise SamePasswordError("La nueva contraseña debe ser distinta a la actual")

        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        await self.users.session.flush()
        await self.refresh_tokens.revoke_all_by_user_id(user_id)
        await self._revoke_access_tokens(user_id)

    async def get_user_for_password_reset(self, email: str) -> User | None:
        """Return the user eligible for a password reset, or None.

        Eligible = exists, is verified, and has a password set. Returns None
        otherwise so the caller can answer generically without revealing whether
        the email is registered (anti-enumeration, like resend-verification).
        """
        user = await self.users.get_by_email(email)
        if user is None or not user.email_verified or user.password_hash is None:
            return None
        return user

    async def reset_password(self, token: str, new_password: str) -> None:
        """Set a new password from a reset token (forgot-password flow).

        The token is single-use: it carries a fingerprint of the password it was
        issued against, so once the password changes the token no longer matches
        and cannot be replayed. Revokes all sessions on success, like
        change_password.
        """
        try:
            payload = decode_token(token)
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("El enlace es inválido o ha expirado") from exc

        if payload.get("type") != "password_reset":
            raise InvalidTokenError("El enlace es inválido o ha expirado")

        user = await self.users.get(int(payload.get("sub", 0)))
        if user is None or user.password_hash is None:
            raise InvalidTokenError("El enlace es inválido o ha expirado")

        # Single-use guard: reject a token minted against a now-stale password.
        if payload.get("pwf") != password_fingerprint(user.password_hash):
            raise InvalidTokenError("El enlace ya fue utilizado o ha expirado")

        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        await self.users.session.flush()
        await self.refresh_tokens.revoke_all_by_user_id(user.id)
        await self._revoke_access_tokens(user.id)
        # A user who forgot their password may have tripped the login throttle;
        # a successful reset proves ownership, so lift the lock (mirrors the
        # reset on a successful login) — otherwise they stay locked out despite
        # the new password.
        if self.login_throttle is not None:
            await self.login_throttle.reset(user.email)

    async def verify_email(self, token: str) -> int:
        """Confirm an email token and return the user id.

        One verification token covers two cases:
        - First-time activation: an unverified, active user becomes verified.
        - Reactivation: a deactivated candidate who registered again is switched
          back on. The caller reactivates the candidate profile (composition at
          the API layer, mirroring delete_me).
        """
        try:
            payload = decode_token(token)
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("Token de verificación inválido o expirado") from exc

        if payload.get("type") != "verification":
            raise InvalidTokenError("Tipo de token no es de verificación")

        user_id = int(payload.get("sub", 0))
        # include_inactive: a returning candidate stays deactivated until this click.
        user = await self.users.get(user_id, include_inactive=True)
        if user is None:
            raise InvalidTokenError("Usuario no encontrado")

        if not user.is_active:
            # Reactivation: bring the account back and re-affirm verification.
            user.is_active = True
            user.email_verified = True
            await self.users.session.flush()
            return user_id

        if user.email_verified:
            raise InvalidTokenError("El enlace ya fue utilizado o ha expirado")

        user.email_verified = True
        await self.users.session.flush()
        return user_id
