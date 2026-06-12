"""Row-level ownership guards for candidate-portal tokens.

RBAC (require_permission) answers "may this role use this endpoint";
these helpers answer "does this row belong to the caller". They only
constrain tokens whose JWT portal claim is "candidate" — staff tokens
pass through untouched, since RBAC already governs their access.
"""

from fastapi import HTTPException, status

from app.core.dependencies import CurrentUser

CANDIDATE_PORTAL = "candidate"


def is_candidate_portal(user: CurrentUser) -> bool:
    return user.portal == CANDIDATE_PORTAL


def require_owner(user: CurrentUser, owner_user_id: int | None) -> None:
    """Reject candidate-portal access to a row owned by another user.

    `owner_user_id` is the auth.users.id that owns the row; passing None
    (unknown owner) is treated as not-owned and rejected for candidates.
    """
    if is_candidate_portal(user) and owner_user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Resource belongs to another user",
        )


def forbid_candidate_portal(user: CurrentUser) -> None:
    """Reject candidate-portal access to unscoped staff-only listings."""
    if is_candidate_portal(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff-only endpoint",
        )
