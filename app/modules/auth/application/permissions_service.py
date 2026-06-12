from app.core.dependencies import CurrentUser
from app.modules.auth.api.permissions_schemas import PermissionCreate, PermissionUpdate
from app.modules.auth.infrastructure.models import Permission
from app.modules.auth.infrastructure.permissions_repository import PermissionRepository
from app.shared.pagination import PageParams


class PermissionError(Exception):
    """Raised on a domain rule violation in the permissions catalog."""


class DuplicatePermissionError(PermissionError):
    pass


class PermissionNotFoundError(PermissionError):
    pass


class PermissionService:
    """Thin application service for the auth.permissions catalog.

    `code` is the stable authorization identifier, so it is unique and cannot be
    changed after creation. Stamps audit columns from the authenticated principal.
    """

    def __init__(self, repository: PermissionRepository) -> None:
        self.repository = repository

    async def list(
        self, params: PageParams, *, module: str | None = None
    ) -> tuple[list[Permission], int]:
        filters = {"module": module} if module else None
        return await self.repository.list(params, filters=filters)

    async def get(self, permission_id: int) -> Permission:
        permission = await self.repository.get(permission_id)
        if permission is None:
            raise PermissionNotFoundError(f"Permission {permission_id} not found")
        return permission

    async def create(self, data: PermissionCreate, actor: CurrentUser) -> Permission:
        existing = await self.repository.get_by_code(data.code)
        if existing is not None:
            raise DuplicatePermissionError(f"Permission '{data.code}' already exists")
        permission = Permission(
            code=data.code,
            name=data.name,
            description=data.description,
            module=data.module,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(permission)

    async def update(
        self, permission_id: int, data: PermissionUpdate, actor: CurrentUser
    ) -> Permission:
        permission = await self.get(permission_id)
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(permission, changes)

    async def delete(self, permission_id: int) -> None:
        permission = await self.get(permission_id)
        await self.repository.soft_delete(permission)
