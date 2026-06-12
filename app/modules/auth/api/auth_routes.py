from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.auth_schemas import (
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    RegisterRequest,
    VerifyRequest,
)
from app.modules.auth.application.auth_service import (
    AuthService,
    AuthTokens,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    EmailAlreadyExistsError,
    InvalidTokenError,
)
from app.modules.auth.infrastructure.repository import (
    RefreshTokenRepository,
    UserRepository,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.candidates_repository import CandidateRepository

router = APIRouter(tags=["auth"])


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
):
    try:
        await service.register_candidate(data.email, data.password, _client_ip(request))
    except EmailAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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
