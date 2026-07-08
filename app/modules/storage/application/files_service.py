from sqlalchemy import func, select

from app.core.config import settings
from app.core.dependencies import CurrentUser
from app.modules.storage.api.files_schemas import FileCreate, FileUpdate
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class FileNotFoundError(Exception):
    pass


class FileDuplicateKeyError(Exception):
    """stored_key already exists — object storage keys must be globally unique."""


class FileOwnershipError(Exception):
    """The client-supplied bucket/stored_key does not originate from the caller's
    own upload flow — rejected to prevent IDOR."""


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

    async def cv_quota_exceeded(
        self,
        user_id: int,
        new_bytes: int,
        *,
        max_count: int,
        max_total_bytes: int,
    ) -> bool:
        """True when one more CV of `new_bytes` would breach the caller's quota.

        Counts only the user's own ACTIVE CV files and bounds both their number and
        their total size, so a candidate can't exhaust storage by uploading many
        large PDFs — a defense per-IP rate limiting can't provide against IP rotation.
        """
        stmt = select(
            func.count(File.id),
            func.coalesce(func.sum(File.size_bytes), 0),
        ).where(
            File.entity_type == "cv",
            File.created_by == user_id,
            File.is_active.is_(True),
        )
        count, total = (await self.repository.session.execute(stmt)).one()
        return (count + 1) > max_count or (int(total) + new_bytes) > max_total_bytes

    async def create(
        self, data: FileCreate, actor: CurrentUser, *, trusted: bool = False
    ) -> File:
        """Persist a File metadata row.

        `trusted=True` is used ONLY by the server-driven /files/upload flow, where
        the bucket and stored_key are generated server-side and are therefore
        safe. Every client-facing path (POST /files) MUST leave `trusted=False`
        so the bucket/stored_key ownership gate runs — otherwise a caller could
        register a File row pointing at an arbitrary bucket + another user's
        stored_key and then download it (IDOR).
        """
        if not trusted:
            await self._assert_owned_key(data, actor)
        f = File(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(f)

    async def _assert_owned_key(self, data: FileCreate, actor: CurrentUser) -> None:
        """Reject an IDOR: a client-supplied bucket/stored_key that does not
        originate from the caller's own /files/upload flow.

        Two gates:
        1. The bucket must be the single application bucket — a client may not
           point a File row at an arbitrary bucket.
        2. The stored_key must reference an object the caller already uploaded.
           /files/upload creates the canonical File row (created_by = caller), so
           a legitimate registration references that row. If no row exists, or it
           belongs to a DIFFERENT user, the caller is trying to claim an object
           that is not theirs.
        """
        if data.bucket != settings.minio_bucket:
            raise FileOwnershipError(
                f"bucket must be '{settings.minio_bucket}'"
            )
        stmt = select(File).where(File.stored_key == data.stored_key)
        existing = (
            await self.repository.session.execute(stmt)
        ).scalar_one_or_none()
        if existing is None or existing.created_by != actor.user_id:
            raise FileOwnershipError(
                "stored_key does not originate from your own upload"
            )

    async def update(self, file_id: int, data: FileUpdate, actor: CurrentUser) -> File:
        f = await self.get(file_id)
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(f, changes)

    async def delete(self, file_id: int) -> None:
        f = await self.get(file_id)
        await self.repository.soft_delete(f)
