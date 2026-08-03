from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import get_client_ip
from app.core.database import get_session
from app.core.security import decode_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=True)


class CurrentUser:
    """Authenticated principal extracted from the access token.

    Carries the user id used to populate audit columns (created_by / updated_by),
    the request IP for ip_created / ip_updated, and the portal CODE (staff |
    candidate) carried as a JWT claim so handlers can branch per portal.
    """

    def __init__(self, user_id: int, ip: str | None, portal: str | None = None) -> None:
        self.user_id = user_id
        self.ip = ip
        self.portal = portal


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> CurrentUser:
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tu sesión expiró. Inicia sesión de nuevo.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tu sesión no es válida. Inicia sesión de nuevo.",
        )

    user_id = int(payload["sub"])

    # Denylist check (security fix 3.4): a password change or self-deactivation
    # records a per-user cutoff; any access token issued at or before it is dead,
    # even though its signature and expiry are still valid.
    issued_at = payload.get("iat")
    denylist = request.app.state.token_denylist
    if issued_at is not None and await denylist.is_user_revoked(user_id, int(issued_at)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tu sesión se cerró por seguridad. Inicia sesión de nuevo.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    ip = get_client_ip(request)
    return CurrentUser(user_id=user_id, ip=ip, portal=payload.get("portal"))


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
