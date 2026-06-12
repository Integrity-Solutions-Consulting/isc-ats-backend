from app.core.dependencies import CurrentUser
from app.modules.storage.api.files_schemas import FileCreate, FileUpdate
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class FileNotFoundError(Exception):
    pass


class FileDuplicateKeyError(Exception):
    """stored_key already exists — object storage keys must be globally unique."""


class FileService:
    def __init__(self, repository: BaseRepository[File]) -> None:
        self.repository = repository

    async def list(
        self,
        params: PageParams,
        *,
        bucket: str | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> tuple[list[File], int]:
        filters = {
            k: v
            for k, v in {
                "bucket": bucket,
                "entity_type": entity_type,
                "entity_id": entity_id,
            }.items()
            if v is not None
        }
        return await self.repository.list(params, filters=filters or None)

    async def get(self, file_id: int) -> File:
        f = await self.repository.get(file_id)
        if f is None:
            raise FileNotFoundError(f"File {file_id} not found")
        return f

    async def create(self, data: FileCreate, actor: CurrentUser) -> File:
        f = File(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(f)

    async def update(self, file_id: int, data: FileUpdate, actor: CurrentUser) -> File:
        f = await self.get(file_id)
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(f, changes)

    async def delete(self, file_id: int) -> None:
        f = await self.get(file_id)
        await self.repository.soft_delete(f)
