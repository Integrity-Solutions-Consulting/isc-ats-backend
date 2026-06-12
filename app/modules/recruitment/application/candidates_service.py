from __future__ import annotations

from typing import Any

from app.core.dependencies import CurrentUser
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.api.candidates_schemas import (
    CandidateCreate,
    CandidateUpdate,
)
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.candidates_expanded import (
    CandidateExpanded,
    CandidatesExpandedRepository,
)
from app.modules.recruitment.infrastructure.candidates_repository import (
    CandidateRepository,
)
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class CandidateNotFoundError(Exception):
    pass


class CandidateReferenceError(Exception):
    """A referenced user, catalog parameter, or file does not exist."""


class DuplicateCandidateError(Exception):
    """The user already has a candidate, or the cedula is taken."""


class CandidateService:
    """CRUD for recruitment.candidates (1:1 with a user).

    The AI-managed columns (parsed_data, cv_embedding, last_parsed_at) are never
    written here — they belong to the CV-parsing pipeline. Validates the user,
    optional catalog parameters and optional file references up front.
    """

    def __init__(
        self,
        repository: CandidateRepository,
        users: BaseRepository[User],
        parameters: BaseRepository[Parameter],
        files: BaseRepository[File],
        expanded: CandidatesExpandedRepository,
    ) -> None:
        self.repository = repository
        self.users = users
        self.parameters = parameters
        self.files = files
        self.expanded = expanded

    async def list(self, params: PageParams) -> tuple[list[Candidate], int]:
        return await self.repository.list(params)

    async def list_expanded(
        self, params: PageParams, *, user_id: int | None = None
    ) -> tuple[list[CandidateExpanded], int]:
        return await self.expanded.list_expanded(params, user_id=user_id)

    async def get(self, candidate_id: int) -> Candidate:
        candidate = await self.repository.get(candidate_id)
        if candidate is None:
            raise CandidateNotFoundError(f"Candidate {candidate_id} not found")
        return candidate

    async def create(self, data: CandidateCreate, actor: CurrentUser) -> Candidate:
        if await self.users.get(data.user_id) is None:
            raise CandidateReferenceError(f"user_id={data.user_id} not found")
        if await self.repository.get_by_user_id(data.user_id) is not None:
            raise DuplicateCandidateError(
                f"User {data.user_id} already has a candidate"
            )
        if data.cedula and await self.repository.get_by_cedula(data.cedula) is not None:
            raise DuplicateCandidateError(f"Cedula {data.cedula} already registered")
        await self._validate_optional_refs(data.model_dump())

        candidate = Candidate(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(candidate)

    async def update(
        self, candidate_id: int, data: CandidateUpdate, actor: CurrentUser
    ) -> Candidate:
        candidate = await self.get(candidate_id)
        changes = data.model_dump(exclude_unset=True)
        new_cedula = changes.get("cedula")
        if new_cedula and new_cedula != candidate.cedula:
            if await self.repository.get_by_cedula(new_cedula) is not None:
                raise DuplicateCandidateError(f"Cedula {new_cedula} already registered")
        await self._validate_optional_refs(changes)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(candidate, changes)

    async def delete(self, candidate_id: int) -> None:
        candidate = await self.get(candidate_id)
        await self.repository.soft_delete(candidate)

    async def _validate_optional_refs(self, values: dict[str, Any]) -> None:
        for field in ("city_id", "province_id", "education_level_id", "career_id", "university_id"):
            await self._assert(self.parameters, values, field)
        for field in ("avatar_file_id", "cv_file_id"):
            await self._assert(self.files, values, field)

    async def _assert(
        self, repo: BaseRepository[Any], values: dict[str, Any], field: str
    ) -> None:
        entity_id = values.get(field)
        if entity_id is not None and await repo.get(entity_id) is None:
            raise CandidateReferenceError(f"{field}={entity_id} not found")
