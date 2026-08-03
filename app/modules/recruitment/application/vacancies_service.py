from collections.abc import Awaitable, Callable
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
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.recruitment.infrastructure.pipeline_repository import (
    PipelineRepository,
)
from app.shared.pagination import PageParams
from app.shared.ports import InUseChecker
from app.shared.repository import BaseRepository

_PUBLISH_PERMISSION = "recruitment.vacancies.publish"

# Fan-out callback that auto-rejects every non-hired active application of a
# vacancy with the given reason, returning the applications actually rejected.
# Bound (at the composition root, vacancies_routes.get_service) to
# ApplicationService.auto_reject_for_vacancy — a plain function reference, not
# the whole service, so VacancyService stays decoupled from ApplicationService's
# full surface (same dependency-inversion shape as InUseChecker above).
ApplicationRejector = Callable[[int, str], Awaitable[list[Application]]]

# Fixed system-generated reason shown to the candidate (email + in-app
# notification) when their application is auto-rejected because the vacancy
# itself closed or was deleted — as opposed to a manual, HR-written reason.
AUTO_REJECT_REASON = (
    "Esta vacante fue cerrada porque ya se cubrieron los recursos solicitados."
)


class VacancyNotFoundError(Exception):
    pass


class VacancyInUseError(Exception):
    """Cannot delete a vacancy that still has a hired candidate on record.

    Cancel the vacancy ('cancelled' status) instead — that preserves the hire
    and its history. Every other active application is auto-rejected as part of
    the delete instead of blocking it (see VacancyService.delete).
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
        application_rejector: ApplicationRejector | None = None,
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
        self.application_rejector = application_rejector
        self._param_repo = ParameterRepository(repository.session)
        # Populated by update()/delete() with the ids of applications that were
        # auto-rejected as a side effect of this call, so the API route (which
        # owns the task queue) can fan out the candidate email + in-app
        # notification for each. Reset at the start of every update()/delete().
        self.last_auto_rejected_application_ids: list[int] = []

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
        self.last_auto_rejected_application_ids = []

        new_status: Parameter | None = None
        if "status_id" in changes and changes["status_id"] != vacancy.status_id:
            new_status = await self._guard_close(vacancy, changes)
            await self._guard_publish(vacancy, changes, caller_can_publish=caller_can_publish)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        updated = await self.repository.update(vacancy, changes)

        # Closing a vacancy auto-rejects every non-hired active application —
        # HR closed it because the openings it still needed are covered, so
        # anyone still in progress is no longer in the running for THIS vacancy.
        closed_now = new_status is not None and new_status.code == "closed"
        if closed_now and self.application_rejector is not None:
            rejected = await self.application_rejector(vacancy_id, AUTO_REJECT_REASON)
            self.last_auto_rejected_application_ids = [app.id for app in rejected]

        return updated

    async def _guard_close(self, vacancy: Vacancy, changes: dict[str, Any]) -> Parameter | None:
        """Block the transition to 'closed' unless every opening is filled.

        Only the 'closed' code is gated — 'cancelled' (and any other status) is a
        valid way to close a vacancy that will not be fully filled. Returns the
        resolved target Parameter (or None) so the caller can detect the 'closed'
        transition without a second lookup.
        """
        new_status = await self.parameters.get(changes["status_id"])
        if new_status is None or new_status.code != "closed":
            return new_status
        pipeline = await self.pipeline.get_pipeline(vacancy.id)
        openings = changes.get("openings", vacancy.openings)
        if pipeline.hired_count < openings:
            raise VacancyCloseError(
                f"No se puede cerrar la vacante: {pipeline.hired_count} de {openings} "
                "vacantes cubiertas. Usa el estado 'Cancelada' para cerrarla sin cubrir."
            )
        return new_status

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
        self.last_auto_rejected_application_ids = []
        if self.applications_checker is not None and await self.applications_checker(vacancy_id):
            raise VacancyInUseError(
                "No se puede eliminar la vacante: tiene candidatos contratados. "
                "Usa el estado 'Cancelada' para cerrarla sin perder el historial."
            )
        await self.repository.soft_delete(vacancy)
        # Deleting removes the vacancy from the running entirely, so every
        # non-hired active application it still had must be auto-rejected too —
        # otherwise they would dangle, active, behind an inactive vacancy.
        if self.application_rejector is not None:
            rejected = await self.application_rejector(vacancy_id, AUTO_REJECT_REASON)
            self.last_auto_rejected_application_ids = [app.id for app in rejected]

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
