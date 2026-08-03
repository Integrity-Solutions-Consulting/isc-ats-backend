import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.dependencies import CurrentUser, CurrentUserDep, SessionDep
from app.modules.auth.infrastructure.authorization_repository import (
    AuthorizationRepository,
)

logger = logging.getLogger(__name__)

# Permission codes are internal contract, not user-facing text: naming the
# missing code on screen tells the user nothing and exposes the RBAC layout.
# The code goes to the log instead, where support can actually use it.
_FORBIDDEN_MESSAGE = "No tienes permiso para realizar esta acción."


async def get_permission_codes(
    current_user: CurrentUserDep, session: SessionDep
) -> set[str]:
    """Effective permission codes of the authenticated user.

    FastAPI caches sub-dependency results within a request, so multiple
    require_permission guards on the same endpoint share a single DB load.
    """
    return await AuthorizationRepository(session).list_permission_codes_for_user(
        current_user.user_id
    )


PermissionCodesDep = Annotated[set[str], Depends(get_permission_codes)]


def require_permission(
    code: str,
) -> Callable[[set[str], CurrentUser], Awaitable[CurrentUser]]:
    """Build a route guard that requires `code` among the user's permissions.

    Usage: add `Depends(require_permission("org.departments.create"))` to a route
    (as a parameter or in the route's `dependencies=[...]`). Raises 403 when the
    permission is absent. The returned checker is a plain coroutine, so it can be
    unit-tested directly with a codes set and a CurrentUser.
    """

    async def checker(
        codes: PermissionCodesDep, current_user: CurrentUserDep
    ) -> CurrentUser:
        if code not in codes:
            logger.info(
                "Permission denied for user %s: missing %s", current_user.user_id, code
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=_FORBIDDEN_MESSAGE,
            )
        return current_user

    return checker


def require_any_permission(
    *codes: str,
) -> Callable[[set[str], CurrentUser], Awaitable[CurrentUser]]:
    """Build a route guard that passes when the user holds ANY of `codes`.

    Use when one endpoint is legitimately reachable by roles holding different
    least-privilege codes — e.g. staff via a broad `recruitment.vacancies.read`
    and the candidate portal via the narrow `recruitment.vacancies.read_stages`.
    Raises 403 when none of the codes are present.
    """
    if not codes:
        raise ValueError("require_any_permission requires at least one permission code")
    required = frozenset(codes)

    async def checker(
        user_codes: PermissionCodesDep, current_user: CurrentUserDep
    ) -> CurrentUser:
        if required.isdisjoint(user_codes):
            logger.info(
                "Permission denied for user %s: missing all of %s",
                current_user.user_id,
                ", ".join(sorted(required)),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=_FORBIDDEN_MESSAGE,
            )
        return current_user

    return checker
