import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)
from app.core.database import async_session_factory
from app.core.dependencies import CurrentUserDep, SessionDep
from app.core.security import create_verification_token
from app.modules.auth.api.auth_schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenResponse,
    VerifyRequest,
)
from app.modules.auth.application.auth_service import (
    AuthService,
    AuthTokens,
    EmailAlreadyExistsError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    InvalidTokenError,
)
from app.modules.auth.infrastructure.repository import (
    RefreshTokenRepository,
    UserRepository,
)
from app.modules.comms.application.email_dispatch_service import EmailDispatchService
from app.modules.comms.application.email_sender import EmailMessage
from app.modules.comms.application.email_templates import render_verification_email
from app.modules.comms.infrastructure.email_sender_factory import build_email_sender
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidates_repository import CandidateRepository

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
                logger.error("Verification email delivery failed for %s — check comms.email_logs", to_email)
        except Exception:
            logger.exception("Unexpected error sending verification email to %s", to_email)
            await session.rollback()


def get_service(session: SessionDep) -> AuthService:
    async def candidate_has_profile(user_id: int) -> bool:
        return await CandidateRepository(session).get_by_user_id(user_id) is not None

    return AuthService(
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        parameters=ParameterRepository(session),
        has_profile_checker=candidate_has_profile,
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
    return request.client.host if request.client else None


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    service: ServiceDep,
    request: Request,
) -> TokenResponse:
    try:
        tokens = await service.login(data.email, data.password, _client_ip(request))
    except InvalidCredentialsError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except EmailNotVerifiedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return _to_response(tokens)


@router.post("/refresh", response_model=TokenResponse)
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
async def register(
    data: RegisterRequest,
    service: ServiceDep,
    request: Request,
    background_tasks: BackgroundTasks,
):
    try:
        user = await service.register_candidate(
            data.email, data.password, _client_ip(request)
        )
    except EmailAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # Send the verification email out-of-band so a slow/failed SMTP call never
    # blocks or fails the registration response.
    background_tasks.add_task(_send_verification_email, user.id, user.email)
    return {"message": "Usuario registrado exitosamente"}


@router.post("/verify", status_code=status.HTTP_200_OK)
async def verify(
    data: VerifyRequest,
    service: ServiceDep,
):
    try:
        await service.verify_email(data.token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"message": "Correo electrónico verificado exitosamente"}


@router.post("/resend-verification", status_code=status.HTTP_200_OK)
async def resend_verification(
    data: ResendVerificationRequest,
    service: ServiceDep,
    background_tasks: BackgroundTasks,
):
    # Generic response regardless of outcome: never reveal whether an email is
    # registered or already verified. The email is only sent for a real,
    # still-unverified account.
    user = await service.get_unverified_user(data.email)
    if user is not None:
        background_tasks.add_task(_send_verification_email, user.id, user.email)
    return {
        "message": "Si el correo está registrado y pendiente de verificación, "
        "te enviamos un nuevo enlace."
    }


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
    # Recruitment layer: deactivate candidate profile (no-op if absent).
    await CandidateRepository(session).deactivate_by_user_id(current_user.user_id)
