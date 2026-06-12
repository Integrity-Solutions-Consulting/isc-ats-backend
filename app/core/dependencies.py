from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

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
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    ip = request.client.host if request.client else None
    return CurrentUser(
        user_id=int(payload["sub"]), ip=ip, portal=payload.get("portal")
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
