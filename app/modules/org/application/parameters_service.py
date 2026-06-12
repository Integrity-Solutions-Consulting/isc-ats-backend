from app.core.dependencies import CurrentUser
from app.modules.org.api.parameters_schemas import ParameterCreate, ParameterUpdate
from app.modules.org.infrastructure.models import Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.pagination import PageParams


class ParameterError(Exception):
    """Raised on a domain rule violation in the parameters catalog."""


class DuplicateParameterError(ParameterError):
    pass


class ParameterNotFoundError(ParameterError):
    pass


class ParameterService:
    """Thin application service for the org.parameters catalog.

    No domain entity / mapper — the ORM model IS the model (pragmatic CRUD).
    Stamps audit columns from the authenticated principal.
    """

    def __init__(self, repository: ParameterRepository) -> None:
        self.repository = repository

    async def list(
        self,
        params: PageParams,
        *,
        type_: str | None = None,
        include_inactive: bool = False,
    ) -> tuple[list[Parameter], int]:
        filters = {"type": type_} if type_ else None
        return await self.repository.list(params, filters=filters, include_inactive=include_inactive)

    async def get(self, parameter_id: int) -> Parameter:
        parameter = await self.repository.get(parameter_id, include_inactive=True)
        if parameter is None:
            raise ParameterNotFoundError(f"Parameter {parameter_id} not found")
        return parameter

    async def create(self, data: ParameterCreate, actor: CurrentUser) -> Parameter:
        existing = await self.repository.get_by_type_and_code(data.type, data.code)
        if existing is not None:
            raise DuplicateParameterError(
                f"Parameter ({data.type}, {data.code}) already exists"
            )
        parameter = Parameter(
            type=data.type,
            code=data.code,
            name=data.name,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(parameter)

    async def update(
        self,
        parameter_id: int,
        data: ParameterUpdate,
        actor: CurrentUser,
    ) -> Parameter:
        parameter = await self.get(parameter_id)
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(parameter, changes)

    async def delete(self, parameter_id: int) -> None:
        parameter = await self.get(parameter_id)
        await self.repository.soft_delete(parameter)
