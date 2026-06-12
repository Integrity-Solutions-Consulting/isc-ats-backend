from sqlalchemy import func, select

from app.core.dependencies import CurrentUser
from app.modules.auth.api.roles_schemas import RoleCreate, RoleUpdate
from app.modules.auth.infrastructure.models import Role, UserRole
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

# System roles are bootstrap-managed and may not be renamed or deleted.
SYSTEM_ROLE_NAMES: frozenset[str] = frozenset({"admin", "candidate"})


class RoleNotFoundError(Exception):
    pass


class RoleDuplicateError(Exception):
    pass


class RoleHasUsersError(Exception):
    pass


class SystemRoleError(Exception):
    pass


class RoleService:
    """CRUD service for the auth.roles catalog with business-rule guards."""

    def __init__(self, repository: BaseRepository[Role]) -> None:
        self.repository = repository

    async def list(self, params: PageParams) -> tuple[list[Role], int]:
        return await self.repository.list(params)

    async def get(self, role_id: int) -> Role:
        role = await self.repository.get(role_id)
        if role is None:
            raise RoleNotFoundError(f"Role {role_id} not found")
        return role

    async def _check_name_unique(self, name: str, exclude_id: int | None = None) -> None:
        """Raise RoleDuplicateError if the name is already taken by an active role."""
        stmt = (
            select(Role)
            .where(Role.name == name)
            .where(Role.is_active.is_(True))
        )
        if exclude_id is not None:
            stmt = stmt.where(Role.id != exclude_id)
        existing = (await self.repository.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            raise RoleDuplicateError(f"A role named '{name}' already exists")

    async def _count_users(self, role_id: int) -> int:
        """Return the number of active users assigned to this role."""
        stmt = (
            select(func.count())
            .select_from(UserRole)
            .where(UserRole.role_id == role_id)
            .where(UserRole.is_active.is_(True))
        )
        return (await self.repository.session.execute(stmt)).scalar_one()

    async def create(self, data: RoleCreate, actor: CurrentUser) -> Role:
        await self._check_name_unique(data.name)
        role = Role(
            name=data.name,
            description=data.description,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(role)

    async def update(self, role_id: int, data: RoleUpdate, actor: CurrentUser) -> Role:
        role = await self.get(role_id)
        if role.name in SYSTEM_ROLE_NAMES:
            raise SystemRoleError(
                f"Role '{role.name}' is a system role and cannot be modified"
            )
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"] != role.name:
            await self._check_name_unique(changes["name"], exclude_id=role_id)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(role, changes)

    async def delete(self, role_id: int) -> None:
        role = await self.get(role_id)
        if role.name in SYSTEM_ROLE_NAMES:
            raise SystemRoleError(
                f"Role '{role.name}' is a system role and cannot be deleted"
            )
        user_count = await self._count_users(role_id)
        if user_count > 0:
            raise RoleHasUsersError(
                f"Role '{role.name}' has {user_count} assigned user(s) — "
                "reassign or remove them before deleting the role"
            )
        await self.repository.soft_delete(role)
