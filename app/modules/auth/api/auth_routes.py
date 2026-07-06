import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.client_ip import get_client_ip
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.dependencies import CurrentUserDep, SessionDep
from app.core.rate_limit import (
    CHANGE_PASSWORD_LIMIT,
    FORGOT_PASSWORD_LIMIT,
    LOGIN_LIMIT,
    REFRESH_LIMIT,
    REGISTER_LIMIT,
    RESEND_LIMIT,
    RESET_PASSWORD_LIMIT,
    limiter,
)
from app.core.security import create_password_reset_token, create_verification_token
from app.core.task_queue import TaskQueueDep, register_task
from app.modules.auth.api.auth_schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyRequest,
)
from app.modules.auth.api.authorization import PermissionCodesDep
from app.modules.auth.application.auth_service import (
    AccountLockedError,
    AuthService,
    AuthTokens,
    EmailAlreadyExistsError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    InvalidTokenError,
    SamePasswordError,
)
from app.modules.auth.infrastructure.repository import (
    RefreshTokenRepository,
    UserRepository,
)
from app.modules.comms.application.email_dispatch_service import EmailDispatchService
from app.modules.comms.application.email_sender import EmailMessage
from app.modules.comms.application.email_templates import (
    render_account_exists_email,
    render_password_reset_email,
    render_reactivation_email,
    render_verification_email,
)
from app.modules.comms.infrastructure.email_sender_factory import build_email_sender
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidates_repository import CandidateRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


async def _send_verification_email(user_id: int, to_email: str) -> None:
    """Background task: build the verification link and send the activation email.

    Runs after the registration response is returned, with its own DB session
    (the request session is already closed). Never propagates — registration has
    already succeeded, so a send failure must not surface as a 500. The failure
    is recorded in comms.email_logs by EmailDispatchService.
    """
    token = create_verification_token(user_id)
    verification_url = f"{settings.frontend_base_url}/api/auth/verify?token={token}"
    rendered = render_verification_email(verification_url)
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
                    "Verification email delivery failed for %s — check comms.email_logs",
                    to_email,
                )
        except Exception:
            logger.exception("Unexpected error sending verification email to %s", to_email)
            await session.rollback()


async def _send_reactivation_email(user_id: int, to_email: str) -> None:
    """Background task: send the reactivation link to a returning candidate.

    Reuses the verification token/endpoint (clicking it switches the account back
    on) but with reactivation-specific copy. Mirrors _send_verification_email:
    own DB session, never propagates — the registration response already returned.
    """
    token = create_verification_token(user_id)
    reactivation_url = f"{settings.frontend_base_url}/api/auth/verify?token={token}"
    rendered = render_reactivation_email(reactivation_url)
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
                    "Reactivation email delivery failed for %s — check comms.email_logs",
                    to_email,
                )
        except Exception:
            logger.exception("Unexpected error sending reactivation email to %s", to_email)
            await session.rollback()


async def _send_account_exists_email(to_email: str) -> None:
    """Background task: tell an existing user that someone tried to re-register.

    Lets the registration endpoint answer generically (no account enumeration)
    while still informing the real owner on a channel only they control. Never
    propagates — this is a courtesy notification.
    """
    login_url = f"{settings.frontend_base_url}/login"
    rendered = render_account_exists_email(login_url)
    message = EmailMessage(
        to_email=to_email,
        subject=rendered.subject,
        html_body=rendered.html_body,
        text_body=rendered.text_body,
    )
    async with async_session_factory() as session:
        try:
            dispatch = EmailDispatchService(session, build_email_sender())
            await dispatch.send(message)
            await session.commit()
        except Exception:
            logger.exception("Failed to send account-exists email to %s", to_email)
            await session.rollback()


async def _send_password_reset_email(user_id: int, to_email: str) -> None:
    """Background task: mint a single-use reset token and email the reset link.

    Runs with its own DB session: it loads the user to read the current password
    hash, which the token is bound to (fingerprint) so the link is single-use.
    Never propagates — the request already answered generically, so a send
    failure must not surface; it's recorded in comms.email_logs.
    """
    async with async_session_factory() as session:
        try:
            user = await UserRepository(session).get(user_id)
            if user is None or user.password_hash is None:
                return
            token = create_password_reset_token(user_id, user.password_hash)
            reset_url = f"{settings.frontend_base_url}/restablecer-contrasena?token={token}"
            rendered = render_password_reset_email(reset_url)
            message = EmailMessage(
                to_email=to_email,
                subject=rendered.subject,
                html_body=rendered.html_body,
                text_body=rendered.text_body,
            )
            dispatch = EmailDispatchService(session, build_email_sender())
            success = await dispatch.send(message)
            await session.commit()
            if not success:
                logger.error(
                    "Password-reset email delivery failed for %s — check comms.email_logs",
                    to_email,
                )
        except Exception:
            logger.exception("Unexpected error sending password-reset email to %s", to_email)
            await session.rollback()


# BUG-27: Note that AuthService instances and their dependency closures (like has_profile_checker)
# are request-scoped because they capture the transactional session. They must not be cached
# or reused beyond the lifecycle of the current request.
def get_service(session: SessionDep, request: Request) -> AuthService:
    async def candidate_has_profile(user_id: int) -> bool:
        return await CandidateRepository(session).get_by_user_id(user_id) is not None

    return AuthService(
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        parameters=ParameterRepository(session),
        has_profile_checker=candidate_has_profile,
        token_denylist=request.app.state.token_denylist,
        login_throttle=request.app.state.login_throttle,
    )


ServiceDep = Annotated[AuthService, Depends(get_service)]


def _to_response(tokens: AuthTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        portal=tokens.portal,
        must_change_password=tokens.must_change_password,
        has_profile=tokens.has_profile,
    )


def _client_ip(request: Request) -> str | None:
    return get_client_ip(request)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(LOGIN_LIMIT)
async def login(
    data: LoginRequest,
    service: ServiceDep,
    request: Request,
) -> TokenResponse:
    try:
        tokens = await service.login(data.email, data.password, _client_ip(request))
    except AccountLockedError as exc:
        # Generic message + Retry-After. Locking applies uniformly to any email,
        # so this never reveals whether the account actually exists.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Demasiados intentos fallidos. Intentá nuevamente más tarde.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except EmailNotVerifiedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return _to_response(tokens)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(REFRESH_LIMIT)
async def refresh(
    data: RefreshRequest,
    service: ServiceDep,
    request: Request,
) -> TokenResponse:
    try:
        tokens = await service.refresh(data.refresh_token, _client_ip(request))
    except InvalidRefreshTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return _to_response(tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshRequest, service: ServiceDep) -> None:
    await service.logout(data.refresh_token)


@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit(REGISTER_LIMIT)
async def register(
    data: RegisterRequest,
    service: ServiceDep,
    request: Request,
    task_queue: TaskQueueDep,
):
    # Generic response in ALL branches so the API never reveals whether an email
    # is already registered (anti-enumeration). New account → verification email;
    # returning (deactivated) candidate → reactivation email; active account →
    # a "you already have an account" email to the real owner.
    try:
        result = await service.register_candidate(
            data.email, data.password, _client_ip(request)
        )
    except EmailAlreadyExistsError:
        await task_queue.enqueue("send_account_exists_email", data.email)
    else:
        # Send the email out-of-band so a slow/failed SMTP call never blocks or
        # fails the registration response.
        task = "send_reactivation_email" if result.reactivation else "send_verification_email"
        await task_queue.enqueue(task, result.user.id, result.user.email)
    return {
        "message": "Si el correo no estaba registrado, te enviamos un enlace de "
        "verificación. Revisa tu bandeja de entrada."
    }


@router.post("/verify", status_code=status.HTTP_200_OK)
async def verify(
    data: VerifyRequest,
    service: ServiceDep,
    session: SessionDep,
):
    try:
        user_id = await service.verify_email(data.token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # Recruitment layer: restore the candidate profile for a returning candidate
    # (no-op for a first-time activation, which has no deactivated profile).
    await CandidateRepository(session).reactivate_by_user_id(user_id)
    return {"message": "Correo electrónico verificado exitosamente"}


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
@limiter.limit(RESEND_LIMIT)
async def resend_verification(
    data: ResendVerificationRequest,
    service: ServiceDep,
    request: Request,
    task_queue: TaskQueueDep,
):
    # Generic response regardless of outcome: never reveal whether an email is
    # registered or already verified. The email is only sent for a real,
    # still-unverified account.
    user = await service.get_unverified_user(data.email)
    if user is not None:
        await task_queue.enqueue("send_verification_email", user.id, user.email)
    return {
        "message": "Si el correo está registrado y pendiente de verificación, "
        "te enviamos un nuevo enlace."
    }


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit(FORGOT_PASSWORD_LIMIT)
async def forgot_password(
    data: ForgotPasswordRequest,
    service: ServiceDep,
    request: Request,
    task_queue: TaskQueueDep,
):
    # Generic response regardless of outcome: never reveal whether an email is
    # registered. The reset email is only sent for a real, eligible account.
    user = await service.get_user_for_password_reset(data.email)
    if user is not None:
        await task_queue.enqueue("send_password_reset_email", user.id, user.email)
    return {
        "message": "Si el correo está registrado, te enviamos un enlace para "
        "restablecer tu contraseña."
    }


@router.post("/reset-password", status_code=status.HTTP_200_OK)
@limiter.limit(RESET_PASSWORD_LIMIT)
async def reset_password(
    data: ResetPasswordRequest,
    service: ServiceDep,
    request: Request,
):
    try:
        await service.reset_password(data.token, data.new_password)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"message": "Contraseña restablecida exitosamente"}


@router.post("/me/change-password", status_code=status.HTTP_200_OK)
@limiter.limit(CHANGE_PASSWORD_LIMIT)
async def change_password(
    data: ChangePasswordRequest,
    current_user: CurrentUserDep,
    service: ServiceDep,
    request: Request,
):
    """Authenticated self password change. Verifies the current password first."""
    try:
        await service.change_password(
            current_user.user_id, data.current_password, data.new_password
        )
    except (InvalidCredentialsError, SamePasswordError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"message": "Contraseña actualizada exitosamente"}


@router.get("/me/permissions")
async def my_permissions(codes: PermissionCodesDep) -> dict[str, list[str]]:
    """The authenticated user's effective permission codes.

    Lets the frontend hide menu entries and guard routes the user cannot use.
    This is UX defense-in-depth only — every endpoint still enforces its own
    permission server-side, so an altered response cannot grant real access.
    """
    return {"permissions": sorted(codes)}


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current_user: CurrentUserDep,
    service: ServiceDep,
    session: SessionDep,
) -> None:
    """Candidate self-deactivation. Staff users are rejected to prevent admin lockout."""
    if current_user.portal != "candidate":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo los usuarios del portal de candidatos pueden eliminar su propia cuenta",
        )
    # Auth layer: deactivate user + revoke all refresh tokens.
    await service.deactivate_user(current_user.user_id)
    # Recruitment layer: deactivate candidate profile (no-op if absent) and all active applications.
    candidate_repo = CandidateRepository(session)
    candidate = await candidate_repo.get_by_user_id(current_user.user_id)
    if candidate is not None:
        await candidate_repo.deactivate_by_user_id(current_user.user_id)
        from sqlalchemy import update
        from app.modules.recruitment.infrastructure.application_models import Application
        stmt = (
            update(Application)
            .where(Application.candidate_id == candidate.id)
            .where(Application.is_active.is_(True))
            .values(is_active=False)
        )
        await session.execute(stmt)


# ── Background task registration (durable queue / inline) ─────────────────────
register_task("send_verification_email", _send_verification_email)
register_task("send_reactivation_email", _send_reactivation_email)
register_task("send_account_exists_email", _send_account_exists_email)
register_task("send_password_reset_email", _send_password_reset_email)
