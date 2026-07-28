from __future__ import annotations

from typing import Any

from app.core.dependencies import CurrentUser
from app.modules.org.infrastructure.models import Parameter, ProcessStage
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
from app.shared.ownership import is_candidate_portal
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class ApplicationNotFoundError(Exception):
    pass


class ApplicationReferenceError(Exception):
    """A referenced vacancy, candidate, stage or parameter does not exist."""


class DuplicateApplicationError(Exception):
    """The candidate already has an active application to this vacancy."""


class RejectionReasonRequiredError(Exception):
    """A manual reject (Kanban move to the rejected column) was submitted with no
    rejection_reason. Free text is mandatory so the candidate always receives a
    concrete explanation — see ApplicationUpdate.rejection_reason."""


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
        items, total = await self.repository.list(params, filters=filters or None)
        await self._attach_vacancy_names(items)
        return items, total

    async def get(self, application_id: int) -> Application:
        application = await self.repository.get(application_id)
        if application is None:
            raise ApplicationNotFoundError(f"Application {application_id} not found")
        await self._attach_vacancy_names([application])
        return application

    async def _attach_vacancy_names(self, items: list[Application]) -> None:
        """Set a transient `vacancy_name` attribute on each item (read by
        ApplicationRead.model_validate — not a persisted column, never flushed).

        Resolved regardless of the vacancy's status (draft/active/closed/paused) —
        BUG-24: a candidate applied to a vacancy that later closed must still see
        its real name in their applications list, not the "Vacante no disponible"
        placeholder the frontend used to fall back to when cross-referencing the
        PUBLIC (active-only) catalog. Only a hard-deleted (is_active=False)
        vacancy resolves to None here; ownership of the underlying application is
        already enforced by the route/service, so exposing a closed vacancy's name
        is safe historical data, not a leak.
        """
        vacancy_ids = {i.vacancy_id for i in items}
        if not vacancy_ids:
            return
        from sqlalchemy import select

        stmt = (
            select(Vacancy.id, Parameter.name)
            .join(Parameter, Parameter.id == Vacancy.vacancy_name_id)
            .where(Vacancy.id.in_(vacancy_ids))
            .where(Vacancy.is_active.is_(True))
        )
        rows = (await self.vacancies.session.execute(stmt)).all()
        names = {vid: name for vid, name in rows}
        for item in items:
            item.vacancy_name = names.get(item.vacancy_id)

    async def create(self, data: ApplicationCreate, actor: CurrentUser) -> Application:
        vacancy = await self.vacancies.get(data.vacancy_id)
        if vacancy is None:
            raise ApplicationReferenceError(f"vacancy_id={data.vacancy_id} not found")
        # Candidate-portal callers may only apply to PUBLISHED vacancies: is_active
        # (already enforced by vacancies.get) AND status code == "active", the same
        # definition the public endpoints use. Raise the not-found error rather than
        # a distinct one so a candidate can't enumerate draft/paused/closed vacancy
        # ids by response shape. Staff are exempt — they place candidates manually.
        if is_candidate_portal(actor):
            vacancy_status = await self.parameters.get(vacancy.status_id)
            if (
                vacancy_status is None
                or vacancy_status.type != "vacancy_status"
                or vacancy_status.code != "active"
            ):
                raise ApplicationReferenceError(f"vacancy_id={data.vacancy_id} not found")
        await self._assert(self.candidates, data.candidate_id, "candidate_id")
        await self._validate_optional(data.model_dump())

        first_stage_id = await self._first_stage_id(vacancy.process_id)
        # Candidate-portal callers may not choose their own application status; force
        # the initial "active" so a client can't self-assign "hired" (mass-assignment
        # defense-in-depth — current_stage_id is likewise server-controlled).
        forced_status_id = await self._candidate_active_status_id(actor)

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
            if forced_status_id is not None:
                changes["status_id"] = forced_status_id
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
            resurrected = await self.repository.update(existing, changes)
            await self._attach_vacancy_names([resurrected])
            return resurrected

        application_data = data.model_dump()
        application_data["current_stage_id"] = first_stage_id
        if forced_status_id is not None:
            application_data["status_id"] = forced_status_id
        application = Application(
            **application_data,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        created = await self.repository.add(application)
        await self._attach_vacancy_names([created])
        return created

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

    async def _candidate_active_status_id(self, actor: CurrentUser) -> int | None:
        """The 'active' application_status id for candidate-portal callers, else None.

        Used to force the status of candidate-created applications so a client-
        supplied status_id (e.g. 'hired') is never trusted. Returns None for staff
        (their supplied status is respected). For a candidate-portal caller it raises
        ApplicationReferenceError when the catalog lacks the 'active' param — the
        control fails CLOSED rather than silently honoring the client's status_id.
        """
        if not is_candidate_portal(actor):
            return None
        active = await self.parameters.get_by_type_and_code(
            "application_status", "active"
        )
        if active is None:
            raise ApplicationReferenceError(
                "application_status 'active' parameter missing"
            )
        return active.id

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

        # Only run the terminal-transition matrix when the caller is actually
        # touching current_stage_id. Falling back to application.current_stage_id
        # when the key is absent from `changes` would treat ANY unrelated PATCH
        # (e.g. editing notes) as a stage transition whenever an application's
        # current_stage_id already happens to be None for a reason other than
        # rejection (e.g. created against a process with zero active stages,
        # see _first_stage_id) — spuriously demanding a rejection_reason, or
        # silently re-deriving status_id, for an update that never touched the
        # stage at all.
        if "current_stage_id" in changes:
            new_stage_id = changes["current_stage_id"]
            existing_status_id = application.status_id

            if new_stage_id is None:
                # Stage set to None → rejection, unless already rejected.
                if existing_status_id != rejected_id and rejected_id is not None:
                    if not data.rejection_reason:
                        raise RejectionReasonRequiredError(
                            "rejection_reason is required when rejecting an application"
                        )
                    changes.update(
                        await self._rejection_changes(application, data.rejection_reason)
                    )
                else:
                    # Already rejected — no real transition; still clear any stale
                    # sub-status (terminal stages have none).
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
        updated = await self.repository.update(application, changes)
        await self._attach_vacancy_names([updated])
        return updated

    async def delete(self, application_id: int) -> None:
        application = await self.get(application_id)
        await self.repository.soft_delete(application)

    async def _rejection_changes(
        self, application: Application, reason: str, rejected_id: int | None = None
    ) -> dict[str, Any]:
        """The field changes that constitute "reject this application with `reason`".

        Single source of truth for the rejection transition, shared by the manual
        Kanban-reject path (update(), above) and the auto-reject fan-out
        (auto_reject_for_vacancy(), below) — both branches must set exactly the
        same fields so a reject is a reject regardless of who triggered it.

        `rejected_id` lets a caller that already resolved the 'rejected'
        application_status id (e.g. a loop over many applications) pass it in
        directly instead of re-querying org.parameters once per application.
        """
        if rejected_id is None:
            rejected_param = await self.parameters.get_by_type_and_code(
                "application_status", "rejected"
            )
            rejected_id = rejected_param.id if rejected_param is not None else None
        changes: dict[str, Any] = {
            # Remember the stage they had reached before current_stage_id is
            # nulled, so the candidate UI can show how far they advanced.
            "rejected_at_stage_id": application.current_stage_id,
            "current_stage_id": None,
            # Terminal stage has no sub-status — clear it.
            "current_status_id": None,
            "rejection_reason": reason,
        }
        if rejected_id is not None:
            changes["status_id"] = rejected_id
        return changes

    async def auto_reject_for_vacancy(self, vacancy_id: int, reason: str) -> list[Application]:
        """Reject every non-hired active application of `vacancy_id` with `reason`.

        Called by VacancyService when a vacancy transitions to 'closed' or is
        deleted, so no active application is left dangling behind a vacancy that
        stopped accepting candidates. Applications already 'hired' are left
        untouched — a hire is a completed, successful outcome independent of the
        vacancy's own lifecycle. Applications already rejected are skipped (a
        no-op that would otherwise re-fire the rejection email/notification).

        Returns the applications that were actually transitioned, so the caller
        (the API route) can fan out the candidate email + in-app notification for
        each — this service has no access to the request's task queue.
        """
        rejected_param = await self.parameters.get_by_type_and_code(
            "application_status", "rejected"
        )
        rejected_id = rejected_param.id if rejected_param is not None else None
        hired_param = await self.parameters.get_by_type_and_code(
            "application_status", "hired"
        )
        hired_id = hired_param.id if hired_param is not None else None

        candidates = await self.repository.list_active_for_vacancy(
            vacancy_id, exclude_status_id=hired_id
        )
        rejected: list[Application] = []
        for application in candidates:
            if rejected_id is not None and application.status_id == rejected_id:
                continue
            changes = await self._rejection_changes(application, reason, rejected_id)
            rejected.append(await self.repository.update(application, changes))
        return rejected

    async def _validate_optional(self, values: dict[str, Any]) -> None:
        await self._assert(self.process_stages, values.get("current_stage_id"), "current_stage_id")
        await self._assert(self.parameters, values.get("current_status_id"), "current_status_id")
        await self._assert(self.parameters, values.get("status_id"), "status_id")

    async def _assert(
        self, repo: BaseRepository[Any], entity_id: int | None, label: str
    ) -> None:
        if entity_id is not None and await repo.get(entity_id) is None:
            raise ApplicationReferenceError(f"{label}={entity_id} not found")
