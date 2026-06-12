from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.dependencies import CurrentUser, CurrentUserDep, SessionDep
from app.modules.auth.infrastructure.authorization_repository import (
    AuthorizationRepository,
)


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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {code}",
            )
        return current_user

    return checker
