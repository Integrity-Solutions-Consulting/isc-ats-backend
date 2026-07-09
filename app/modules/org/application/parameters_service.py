from app.core.dependencies import CurrentUser
from app.modules.org.api.parameters_schemas import ParameterCreate, ParameterUpdate
from app.modules.org.infrastructure.models import Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.shared.pagination import PageParams
from app.shared.ports import InUseChecker


class ParameterError(Exception):
    """Raised on a domain rule violation in the parameters catalog."""


class DuplicateParameterError(ParameterError):
    pass


class ParameterNotFoundError(ParameterError):
    pass


class ParameterInUseError(ParameterError):
    """Cannot delete a parameter still referenced by an active record."""


class ParameterTypeForbiddenError(ParameterError):
    """Caller is restricted to a subset of parameter types and the requested/effective
    type falls outside that allowlist (spec R8, → 403)."""


class ParameterService:
    """Thin application service for the org.parameters catalog.

    No domain entity / mapper — the ORM model IS the model (pragmatic CRUD).
    Stamps audit columns from the authenticated principal.
    """

    def __init__(
        self,
        repository: ParameterRepository,
        in_use_checker: InUseChecker | None = None,
    ) -> None:
        self.repository = repository
        self.in_use_checker = in_use_checker

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

    async def create(
        self,
        data: ParameterCreate,
        actor: CurrentUser,
        *,
        restrict_to_types: set[str] | None = None,
    ) -> Parameter:
        if restrict_to_types is not None and data.type not in restrict_to_types:
            raise ParameterTypeForbiddenError(
                f"Not allowed to create parameters of type '{data.type}'"
            )
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
        *,
        restrict_to_types: set[str] | None = None,
    ) -> Parameter:
        parameter = await self.get(parameter_id)
        if restrict_to_types is not None:
            effective_type = data.type if data.type is not None else parameter.type
            if effective_type not in restrict_to_types:
                raise ParameterTypeForbiddenError(
                    f"Not allowed to update parameters of type '{effective_type}'"
                )
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(parameter, changes)

    async def delete(self, parameter_id: int) -> None:
        parameter = await self.get(parameter_id)
        if self.in_use_checker is not None and await self.in_use_checker(parameter_id):
            raise ParameterInUseError(
                "No se puede eliminar el parámetro: está en uso por uno o más "
                "registros activos del sistema."
            )
        await self.repository.soft_delete(parameter)
