from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import Permission, Role, RolePermission
from app.modules.auth.infrastructure.permissions_repository import PermissionRepository
from app.modules.auth.infrastructure.role_permissions_repository import (
    RolePermissionRepository,
)
from app.shared.repository import BaseRepository


class RolePermissionError(Exception):
    """Base error for role-permission grant rules."""


class RoleNotFoundError(RolePermissionError):
    pass


class PermissionNotFoundError(RolePermissionError):
    pass


class PermissionAlreadyGrantedError(RolePermissionError):
    pass


class PermissionGrantNotFoundError(RolePermissionError):
    pass


class RolePermissionService:
    """Grant / revoke permissions for a role through auth.role_permissions.

    Both endpoints validate the role and permission against their catalogs so the
    API returns a clear 404 instead of an opaque integrity violation.
    """

    def __init__(
        self,
        links: RolePermissionRepository,
        roles: BaseRepository[Role],
        permissions: PermissionRepository,
    ) -> None:
        self.links = links
        self.roles = roles
        self.permissions = permissions

    async def list_permissions(self, role_id: int) -> list[Permission]:
        await self._assert_role_exists(role_id)
        return await self.links.list_permissions_for_role(role_id)

    async def grant(
        self, role_id: int, permission_id: int, actor: CurrentUser
    ) -> Permission:
        await self._assert_role_exists(role_id)
        permission = await self._get_permission(permission_id)

        existing = await self.links.get(role_id, permission_id, include_inactive=True)
        if existing is not None:
            if existing.is_active:
                raise PermissionAlreadyGrantedError(
                    f"Permission {permission_id} is already granted to role {role_id}"
                )
            existing.is_active = True
            existing.updated_by = actor.user_id
            existing.ip_updated = actor.ip
            await self.links.save(existing)
            return permission

        link = RolePermission(
            role_id=role_id,
            permission_id=permission_id,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        await self.links.add(link)
        return permission

    async def revoke(self, role_id: int, permission_id: int) -> None:
        link = await self.links.get(role_id, permission_id)
        if link is None:
            raise PermissionGrantNotFoundError(
                f"Permission {permission_id} is not granted to role {role_id}"
            )
        await self.links.soft_delete(link)

    async def _assert_role_exists(self, role_id: int) -> None:
        if await self.roles.get(role_id) is None:
            raise RoleNotFoundError(f"Role {role_id} not found")

    async def _get_permission(self, permission_id: int) -> Permission:
        permission = await self.permissions.get(permission_id)
        if permission is None:
            raise PermissionNotFoundError(f"Permission {permission_id} not found")
        return permission
