from datetime import UTC, datetime
from typing import Any

from app.core.dependencies import CurrentUser
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProfileTemplate,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.api.vacancies_schemas import (
    VacancyCreate,
    VacancyUpdate,
)
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.pipeline_repository import (
    PipelineRepository,
)
from app.shared.pagination import PageParams
from app.shared.ports import InUseChecker
from app.shared.repository import BaseRepository

_PUBLISH_PERMISSION = "recruitment.vacancies.publish"


class VacancyNotFoundError(Exception):
    pass


class VacancyInUseError(Exception):
    """Cannot delete a vacancy that still has active applications.

    Cancel the vacancy ('cancelled' status) instead — that preserves the
    applications and their history.
    """


class VacancyReferenceError(Exception):
    """A referenced catalog row or org entity does not exist (or is inactive)."""


class VacancyCloseError(Exception):
    """Cannot move a vacancy to 'closed' while openings remain unfilled.

    Use the 'cancelled' status to close a vacancy without filling every opening.
    """


class VacancyPublishForbiddenError(Exception):
    """Caller does not hold recruitment.vacancies.publish — cannot transition to 'active'."""


class VacancyProcessRequiredError(Exception):
    """A vacancy cannot go 'active' without a linked selection process."""


class VacancyService:
    """CRUD for recruitment.vacancies, validating its many references up front.

    Each FK is checked against the live catalogs so the API answers a clear 422
    naming the offending reference, instead of an opaque integrity violation.
    """

    def __init__(
        self,
        repository: BaseRepository[Vacancy],
        parameters: BaseRepository[Parameter],
        client_companies: BaseRepository[ClientCompany],
        contacts: BaseRepository[Contact],
        departments: BaseRepository[Department],
        processes: BaseRepository[Process],
        profile_templates: BaseRepository[ProfileTemplate],
        pipeline: PipelineRepository,
        applications_checker: InUseChecker | None = None,
    ) -> None:
        self.repository = repository
        self.parameters = parameters
        self.client_companies = client_companies
        self.contacts = contacts
        self.departments = departments
        self.processes = processes
        self.profile_templates = profile_templates
        self.pipeline = pipeline
        self.applications_checker = applications_checker
        self._param_repo = ParameterRepository(repository.session)

    async def list(
        self,
        params: PageParams,
        *,
        client_company_id: int | None = None,
        status_id: int | None = None,
        department_id: int | None = None,
    ) -> tuple[list[Vacancy], int]:
        filters = {
            k: v
            for k, v in {
                "client_company_id": client_company_id,
                "status_id": status_id,
                "department_id": department_id,
            }.items()
            if v is not None
        }
        return await self.repository.list(params, filters=filters or None)

    async def get(self, vacancy_id: int) -> Vacancy:
        vacancy = await self.repository.get(vacancy_id, include_inactive=True)
        if vacancy is None:
            raise VacancyNotFoundError(f"La vacante con ID {vacancy_id} no fue encontrada.")
        return vacancy

    async def create(
        self,
        data: VacancyCreate,
        actor: CurrentUser,
        *,
        caller_permission_codes: set[str] | None = None,
    ) -> Vacancy:
        caller_can_publish = _PUBLISH_PERMISSION in (caller_permission_codes or set())

        # Solicitud-forcing (R3): non-publisher → override status + null process
        payload = data.model_dump()
        if not caller_can_publish:
            solicitud = await self._param_repo.get_by_type_and_code("vacancy_status", "solicitud")
            if solicitud is not None:
                payload["status_id"] = solicitud.id
            payload["process_id"] = None

        await self._validate_refs(payload)
        await self._guard_publish(None, payload, caller_can_publish=caller_can_publish)
        vacancy = Vacancy(
            **payload,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(vacancy)

    async def update(
        self,
        vacancy_id: int,
        data: VacancyUpdate,
        actor: CurrentUser,
        *,
        caller_permission_codes: set[str] | None = None,
    ) -> Vacancy:
        caller_can_publish = _PUBLISH_PERMISSION in (caller_permission_codes or set())
        vacancy = await self.get(vacancy_id)
        changes = data.model_dump(exclude_unset=True)
        await self._validate_refs(changes)
        if "status_id" in changes and changes["status_id"] != vacancy.status_id:
            await self._guard_close(vacancy, changes)
            await self._guard_publish(vacancy, changes, caller_can_publish=caller_can_publish)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(vacancy, changes)

    async def _guard_close(self, vacancy: Vacancy, changes: dict[str, Any]) -> None:
        """Block the transition to 'closed' unless every opening is filled.

        Only the 'closed' code is gated — 'cancelled' (and any other status) is a
        valid way to close a vacancy that will not be fully filled.
        """
        new_status = await self.parameters.get(changes["status_id"])
        if new_status is None or new_status.code != "closed":
            return
        pipeline = await self.pipeline.get_pipeline(vacancy.id)
        openings = changes.get("openings", vacancy.openings)
        if pipeline.hired_count < openings:
            raise VacancyCloseError(
                f"No se puede cerrar la vacante: {pipeline.hired_count} de {openings} "
                "vacantes cubiertas. Usá el estado 'Cancelada' para cerrarla sin cubrir."
            )

    async def _guard_publish(
        self,
        vacancy: Vacancy | None,
        changes: dict[str, Any],
        *,
        caller_can_publish: bool,
    ) -> None:
        """Enforce publish-to-active rules (R4).

        Called from both create (vacancy=None) and update (vacancy=existing row)
        whenever the resolved target status_id is present in changes.

        - If the target status code is NOT 'active' → no-op.
        - If caller cannot publish → VacancyPublishForbiddenError (→ 403).
        - If process_id is None (changes + existing row) → VacancyProcessRequiredError (→ 422).
        - On a valid publish → set published_at = now(UTC) in changes.
        """
        status_id = changes.get("status_id")
        if status_id is None:
            return

        new_status = await self.parameters.get(status_id)
        if new_status is None or new_status.code != "active":
            return

        if not caller_can_publish:
            raise VacancyPublishForbiddenError(
                "Se requiere el permiso 'recruitment.vacancies.publish' para activar una vacante."
            )

        # Resolve effective process_id: changes override the existing vacancy value
        effective_process_id = changes.get("process_id", vacancy.process_id if vacancy else None)
        if effective_process_id is None:
            raise VacancyProcessRequiredError(
                "No se puede activar la vacante: debe tener un proceso de selección asignado."
            )

        changes["published_at"] = datetime.now(UTC)

    async def delete(self, vacancy_id: int) -> None:
        vacancy = await self.get(vacancy_id)
        if self.applications_checker is not None and await self.applications_checker(vacancy_id):
            raise VacancyInUseError(
                "No se puede eliminar la vacante: tiene postulaciones activas. "
                "Usá el estado 'Cancelada' para cerrarla sin perder el historial."
            )
        await self.repository.soft_delete(vacancy)

    async def _validate_refs(self, values: dict[str, Any]) -> None:
        """Validate any FK present in `values` against its catalog."""
        param_fields = (
            "vacancy_name_id",
            "career_id",
            "city_id",
            "work_mode_id",
            "resource_level_id",
            "status_id",
        )
        for field in param_fields:
            await self._assert(self.parameters, values, field)
        await self._assert(self.client_companies, values, "client_company_id")
        await self._assert(self.contacts, values, "contact_id")
        await self._assert(self.departments, values, "department_id")
        await self._assert(self.processes, values, "process_id")
        await self._assert(self.profile_templates, values, "profile_template_id")

    async def _assert(self, repo: BaseRepository[Any], values: dict[str, Any], field: str) -> None:
        FIELD_LABELS_ES = {
            "vacancy_name_id": "El cargo",
            "career_id": "La carrera",
            "city_id": "La ciudad",
            "work_mode_id": "La modalidad de trabajo",
            "resource_level_id": "El nivel de experiencia",
            "status_id": "El estado de la vacante",
            "client_company_id": "El cliente / empresa",
            "contact_id": "El contacto",
            "department_id": "El departamento",
            "process_id": "El proceso de selección",
            "profile_template_id": "La plantilla de perfil",
        }
        entity_id = values.get(field)
        if entity_id is not None and await repo.get(entity_id) is None:
            friendly_name = FIELD_LABELS_ES.get(field, field)
            raise VacancyReferenceError(
                f"{friendly_name} (ID: {entity_id}) seleccionado no es válido "
                "o no existe en el catálogo."
            )
