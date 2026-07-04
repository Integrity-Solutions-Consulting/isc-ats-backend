from typing import Any

from app.core.dependencies import CurrentUser
from app.modules.org.infrastructure.models import ProcessStage
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.applications_schemas import (
    ApplicationCreate,
    ApplicationUpdate,
)
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.applications_repository import (
    ApplicationRepository,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class ApplicationNotFoundError(Exception):
    pass


class ApplicationReferenceError(Exception):
    """A referenced vacancy, candidate, stage or parameter does not exist."""


class DuplicateApplicationError(Exception):
    """The candidate already has an active application to this vacancy."""


class ApplicationService:
    """CRUD for recruitment.applications.

    One application per (vacancy, candidate). A withdrawn (soft-deleted)
    application is resurrected on re-apply instead of inserting a row that would
    violate the unique index. match_score / match_summary are AI-managed.
    """

    def __init__(
        self,
        repository: ApplicationRepository,
        vacancies: BaseRepository[Vacancy],
        candidates: BaseRepository[Candidate],
        process_stages: BaseRepository[ProcessStage],
        parameters: ParameterRepository,
    ) -> None:
        self.repository = repository
        self.vacancies = vacancies
        self.candidates = candidates
        self.process_stages = process_stages
        self.parameters = parameters

    async def list(
        self,
        params: PageParams,
        *,
        vacancy_id: int | None = None,
        candidate_id: int | None = None,
        status_id: int | None = None,
    ) -> tuple[list[Application], int]:
        filters = {
            k: v
            for k, v in {
                "vacancy_id": vacancy_id,
                "candidate_id": candidate_id,
                "status_id": status_id,
            }.items()
            if v is not None
        }
        return await self.repository.list(params, filters=filters or None)

    async def get(self, application_id: int) -> Application:
        application = await self.repository.get(application_id)
        if application is None:
            raise ApplicationNotFoundError(f"Application {application_id} not found")
        return application

    async def create(self, data: ApplicationCreate, actor: CurrentUser) -> Application:
        vacancy = await self.vacancies.get(data.vacancy_id)
        if vacancy is None:
            raise ApplicationReferenceError(f"vacancy_id={data.vacancy_id} not found")
        await self._assert(self.candidates, data.candidate_id, "candidate_id")
        await self._validate_optional(data.model_dump())

        first_stage_id = await self._first_stage_id(vacancy.process_id)

        existing = await self.repository.get_by_vacancy_and_candidate(
            data.vacancy_id, data.candidate_id
        )
        if existing is not None:
            if existing.is_active:
                raise DuplicateApplicationError(
                    f"Candidate {data.candidate_id} already applied to vacancy "
                    f"{data.vacancy_id}"
                )
            changes = data.model_dump()
            changes["is_active"] = True
            changes["current_stage_id"] = first_stage_id
            # Resurrecting a withdrawn application must start clean: clear the
            # rejection stage marker and the AI-computed match fields left over
            # from the previous lifecycle, and reset the sub-status to the initial
            # (none) state so the row does not carry stale terminal data.
            changes["rejected_at_stage_id"] = None
            changes["match_score"] = None
            changes["match_summary"] = None
            changes["current_status_id"] = None
            changes["updated_by"] = actor.user_id
            changes["ip_updated"] = actor.ip
            return await self.repository.update(existing, changes)

        application_data = data.model_dump()
        application_data["current_stage_id"] = first_stage_id
        application = Application(
            **application_data,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(application)

    async def _assert_stage_in_process(self, vacancy_id: int, stage_id: int) -> None:
        """Reject a current_stage_id that does not belong to the vacancy's process."""
        vacancy = await self.vacancies.get(vacancy_id)
        if vacancy is None:
            raise ApplicationReferenceError(f"vacancy_id={vacancy_id} not found")
        stage = await self.process_stages.get(stage_id)
        if stage is None or stage.process_id != vacancy.process_id:
            raise ApplicationReferenceError(
                f"current_stage_id={stage_id} does not belong to the vacancy's process"
            )

    async def _first_stage_id(self, process_id: int) -> int | None:
        from sqlalchemy import select
        stmt = (
            select(ProcessStage.id)
            .where(ProcessStage.process_id == process_id)
            .where(ProcessStage.is_active.is_(True))
            .order_by(ProcessStage.order)
            .limit(1)
        )
        session = self.process_stages.session
        return (await session.execute(stmt)).scalar_one_or_none()

    async def update(
        self, application_id: int, data: ApplicationUpdate, actor: CurrentUser
    ) -> Application:
        application = await self.get(application_id)
        changes = data.model_dump(exclude_unset=True)
        await self._validate_optional(changes)

        # A stage may only be set to one that belongs to THIS application's
        # vacancy process — otherwise the Kanban column would jump to a stage
        # from an unrelated process. Existence alone (checked in
        # _validate_optional) is not enough.
        if changes.get("current_stage_id") is not None:
            await self._assert_stage_in_process(
                application.vacancy_id, changes["current_stage_id"]
            )

        # ── Terminal-transition matrix ────────────────────────────────────────
        # Resolve the three application_status param ids (cached lazily per call).
        rejected_param = await self.parameters.get_by_type_and_code(
            "application_status", "rejected"
        )
        hired_param = await self.parameters.get_by_type_and_code(
            "application_status", "hired"
        )
        active_param = await self.parameters.get_by_type_and_code(
            "application_status", "active"
        )

        rejected_id = rejected_param.id if rejected_param is not None else None
        hired_id = hired_param.id if hired_param is not None else None
        active_id = active_param.id if active_param is not None else None

        # Determine the resulting stage_id after this update.
        if "current_stage_id" in changes:
            new_stage_id = changes["current_stage_id"]
        else:
            new_stage_id = application.current_stage_id

        existing_status_id = application.status_id

        if new_stage_id is None:
            # Stage set to None → rejection, unless already rejected.
            if existing_status_id != rejected_id and rejected_id is not None:
                changes["status_id"] = rejected_id
                # Remember the stage they had reached before current_stage_id is
                # nulled, so the candidate UI can show how far they advanced.
                changes["rejected_at_stage_id"] = application.current_stage_id
            # Terminal stage has no sub-status — clear it.
            changes["current_status_id"] = None
        else:
            # Stage is being set to a concrete stage — inspect is_final_positive.
            new_stage = await self.process_stages.get(new_stage_id)
            if new_stage is not None and new_stage.is_final_positive:
                # Moving to (or staying on) a final-positive stage → hired.
                if hired_id is not None:
                    changes["status_id"] = hired_id
                # Terminal stage has no sub-status — clear it.
                changes["current_status_id"] = None
            elif existing_status_id == hired_id and active_id is not None:
                # Moving OFF a final-positive stage to a non-final stage → reactivate.
                changes["status_id"] = active_id
            # Otherwise (normal non-terminal move): status_id unchanged.

        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(application, changes)

    async def delete(self, application_id: int) -> None:
        application = await self.get(application_id)
        await self.repository.soft_delete(application)

    async def _validate_optional(self, values: dict[str, Any]) -> None:
        await self._assert(self.process_stages, values.get("current_stage_id"), "current_stage_id")
        await self._assert(self.parameters, values.get("current_status_id"), "current_status_id")
        await self._assert(self.parameters, values.get("status_id"), "status_id")

    async def _assert(
        self, repo: BaseRepository[Any], entity_id: int | None, label: str
    ) -> None:
        if entity_id is not None and await repo.get(entity_id) is None:
            raise ApplicationReferenceError(f"{label}={entity_id} not found")
