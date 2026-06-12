from typing import Any

from app.core.dependencies import CurrentUser
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.api.application_documents_schemas import (
    ApplicationDocumentCreate,
    ApplicationDocumentUpdate,
)
from app.modules.recruitment.infrastructure.application_models import (
    Application,
    ApplicationDocument,
)
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class ApplicationDocumentNotFoundError(Exception):
    pass


class ApplicationDocumentReferenceError(Exception):
    """A referenced application, file, or status parameter does not exist."""


class ApplicationDocumentService:
    """Thin CRUD for recruitment.application_documents with FK validation."""

    def __init__(
        self,
        repository: BaseRepository[ApplicationDocument],
        applications: BaseRepository[Application],
        files: BaseRepository[File],
        parameters: BaseRepository[Parameter],
    ) -> None:
        self.repository = repository
        self.applications = applications
        self.files = files
        self.parameters = parameters

    async def list(
        self, params: PageParams, *, application_id: int | None = None
    ) -> tuple[list[ApplicationDocument], int]:
        filters = {"application_id": application_id} if application_id else None
        return await self.repository.list(params, filters=filters)

    async def get(self, document_id: int) -> ApplicationDocument:
        document = await self.repository.get(document_id)
        if document is None:
            raise ApplicationDocumentNotFoundError(f"Document {document_id} not found")
        return document

    async def create(
        self, data: ApplicationDocumentCreate, actor: CurrentUser
    ) -> ApplicationDocument:
        await self._assert(self.applications, data.application_id, "application_id")
        await self._validate_optional(data.model_dump())
        document = ApplicationDocument(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(document)

    async def update(
        self, document_id: int, data: ApplicationDocumentUpdate, actor: CurrentUser
    ) -> ApplicationDocument:
        document = await self.get(document_id)
        changes = data.model_dump(exclude_unset=True)
        await self._validate_optional(changes)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(document, changes)

    async def delete(self, document_id: int) -> None:
        document = await self.get(document_id)
        await self.repository.soft_delete(document)

    async def _validate_optional(self, values: dict[str, Any]) -> None:
        await self._assert(self.parameters, values.get("status_id"), "status_id")
        await self._assert(self.files, values.get("file_id"), "file_id")

    async def _assert(
        self, repo: BaseRepository[Any], entity_id: int | None, label: str
    ) -> None:
        if entity_id is not None and await repo.get(entity_id) is None:
            raise ApplicationDocumentReferenceError(f"{label}={entity_id} not found")
