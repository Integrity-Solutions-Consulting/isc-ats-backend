from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import Role, UserRole
from app.modules.auth.infrastructure.repository import UserRepository
from app.modules.auth.infrastructure.user_roles_repository import UserRoleRepository
from app.shared.repository import BaseRepository


class UserRoleError(Exception):
    """Base error for user-role assignment rules."""


class UserNotFoundError(UserRoleError):
    pass


class RoleNotFoundError(UserRoleError):
    pass


class RoleAlreadyAssignedError(UserRoleError):
    pass


class RoleAssignmentNotFoundError(UserRoleError):
    pass


class UserRoleService:
    """Assign / revoke roles for a user through the auth.user_roles junction.

    Both endpoints are validated against the live users and roles catalogs so the
    API returns a clear 404 instead of an opaque integrity violation.
    """

    def __init__(
        self,
        links: UserRoleRepository,
        users: UserRepository,
        roles: BaseRepository[Role],
    ) -> None:
        self.links = links
        self.users = users
        self.roles = roles

    async def list_roles(self, user_id: int) -> list[Role]:
        await self._assert_user_exists(user_id)
        return await self.links.list_roles_for_user(user_id)

    async def assign(self, user_id: int, role_id: int, actor: CurrentUser) -> Role:
        await self._assert_user_exists(user_id)
        role = await self._get_role(role_id)

        existing = await self.links.get(user_id, role_id, include_inactive=True)
        if existing is not None:
            if existing.is_active:
                raise RoleAlreadyAssignedError(
                    f"Role {role_id} is already assigned to user {user_id}"
                )
            existing.is_active = True
            existing.updated_by = actor.user_id
            existing.ip_updated = actor.ip
            await self.links.save(existing)
            return role

        link = UserRole(
            user_id=user_id,
            role_id=role_id,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        await self.links.add(link)
        return role

    async def revoke(self, user_id: int, role_id: int) -> None:
        link = await self.links.get(user_id, role_id)
        if link is None:
            raise RoleAssignmentNotFoundError(
                f"Role {role_id} is not assigned to user {user_id}"
            )
        await self.links.soft_delete(link)

    async def _assert_user_exists(self, user_id: int) -> None:
        if await self.users.get(user_id) is None:
            raise UserNotFoundError(f"User {user_id} not found")

    async def _get_role(self, role_id: int) -> Role:
        role = await self.roles.get(role_id)
        if role is None:
            raise RoleNotFoundError(f"Role {role_id} not found")
        return role
