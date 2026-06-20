from app.core.dependencies import CurrentUser
from app.modules.org.api.process_stages_schemas import (
    ProcessStageCreate,
    ProcessStageUpdate,
)
from app.modules.org.infrastructure.models import Parameter, Process, ProcessStage
from app.modules.org.infrastructure.process_stages_repository import (
    ProcessStageRepository,
)
from app.shared.ports import InUseChecker
from app.shared.repository import BaseRepository

STAGE_PARAMETER_TYPE = "stage"


class ProcessStageNotFoundError(Exception):
    pass


class ProcessStageInUseError(Exception):
    """Cannot delete a stage that still holds an active application."""


class ProcessStageReferenceError(Exception):
    """Referenced process is missing, or stage_id is not a 'stage' parameter."""


class DuplicateStageError(Exception):
    """The stage is already in the process, or its order position is taken."""


class ProcessStageService:
    """Manages the ordered stages of a process, enforcing both uniqueness rules."""

    def __init__(
        self,
        repository: ProcessStageRepository,
        processes: BaseRepository[Process],
        parameters: BaseRepository[Parameter],
        in_use_checker: InUseChecker | None = None,
    ) -> None:
        self.repository = repository
        self.processes = processes
        self.parameters = parameters
        self.in_use_checker = in_use_checker

    async def list_by_process(self, process_id: int) -> list[ProcessStage]:
        return await self.repository.list_by_process(process_id)

    async def get(self, stage_id: int) -> ProcessStage:
        stage = await self.repository.get(stage_id)
        if stage is None:
            raise ProcessStageNotFoundError(f"ProcessStage {stage_id} not found")
        return stage

    async def _assert_process(self, process_id: int) -> None:
        if await self.processes.get(process_id) is None:
            raise ProcessStageReferenceError(f"Process {process_id} not found")

    async def _assert_stage_parameter(self, stage_id: int) -> None:
        parameter = await self.parameters.get(stage_id)
        if parameter is None or parameter.type != STAGE_PARAMETER_TYPE:
            raise ProcessStageReferenceError(
                f"Parameter {stage_id} is not a '{STAGE_PARAMETER_TYPE}'"
            )

    async def _assert_unique(
        self,
        process_id: int,
        stage_id: int,
        order: int,
        *,
        exclude_id: int | None = None,
    ) -> None:
        if await self.repository.find_by_stage(
            process_id, stage_id, exclude_id=exclude_id
        ):
            raise DuplicateStageError("Stage already added to this process")
        if await self.repository.find_by_order(
            process_id, order, exclude_id=exclude_id
        ):
            raise DuplicateStageError(f"Order {order} already taken in this process")

    async def create(self, data: ProcessStageCreate, actor: CurrentUser) -> ProcessStage:
        await self._assert_process(data.process_id)
        await self._assert_stage_parameter(data.stage_id)
        await self._assert_unique(data.process_id, data.stage_id, data.order)
        stage = ProcessStage(
            process_id=data.process_id,
            stage_id=data.stage_id,
            order=data.order,
            is_final_positive=data.is_final_positive,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(stage)

    async def update(
        self, process_stage_id: int, data: ProcessStageUpdate, actor: CurrentUser
    ) -> ProcessStage:
        stage = await self.get(process_stage_id)
        changes = data.model_dump(exclude_unset=True)

        if "stage_id" in changes:
            await self._assert_stage_parameter(changes["stage_id"])

        effective_stage = changes.get("stage_id", stage.stage_id)
        effective_order = changes.get("order", stage.order)
        if "stage_id" in changes or "order" in changes:
            await self._assert_unique(
                stage.process_id,
                effective_stage,
                effective_order,
                exclude_id=stage.id,
            )

        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(stage, changes)

    async def delete(self, process_stage_id: int) -> None:
        stage = await self.get(process_stage_id)
        if self.in_use_checker is not None and await self.in_use_checker(
            process_stage_id
        ):
            raise ProcessStageInUseError(
                "No se puede eliminar la etapa: hay postulaciones activas en ella."
            )
        await self.repository.soft_delete(stage)
