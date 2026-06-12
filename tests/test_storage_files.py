import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.storage.api.files_schemas import FileCreate, FileUpdate
from app.modules.storage.application.files_service import FileNotFoundError, FileService
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> FileService:
    return FileService(BaseRepository(session, File))


def _payload(**overrides) -> FileCreate:
    return FileCreate(
        original_name="cv.pdf",
        stored_key=uuid.uuid4().hex,
        bucket="candidates",
        **overrides,
    )


async def test_create_file(session: AsyncSession) -> None:
    f = await _service(session).create(_payload(mime_type="application/pdf", size_bytes=1024), ACTOR)

    assert f.id is not None
    assert f.bucket == "candidates"
    assert f.is_public is False
    assert f.created_by == ACTOR.user_id


async def test_get_file_not_found(session: AsyncSession) -> None:
    with pytest.raises(FileNotFoundError):
        await _service(session).get(999999)


async def test_update_file_entity_link(session: AsyncSession) -> None:
    svc = _service(session)
    f = await svc.create(_payload(), ACTOR)
    updated = await svc.update(f.id, FileUpdate(entity_type="candidate", entity_id=42), ACTOR)

    assert updated.entity_type == "candidate"
    assert updated.entity_id == 42
    assert updated.updated_by == ACTOR.user_id


async def test_delete_file_soft_deletes(session: AsyncSession) -> None:
    svc = _service(session)
    f = await svc.create(_payload(), ACTOR)
    await svc.delete(f.id)

    with pytest.raises(FileNotFoundError):
        await svc.get(f.id)


async def test_list_files_filtered_by_bucket(session: AsyncSession) -> None:
    svc = _service(session)
    await svc.create(_payload(), ACTOR)
    await svc.create(FileCreate(original_name="promo.png", stored_key=uuid.uuid4().hex, bucket="promos"), ACTOR)

    items, total = await svc.list(PageParams(page=1, size=20), bucket="candidates")
    assert total >= 1
    assert all(i.bucket == "candidates" for i in items)


async def test_list_files_filtered_by_entity(session: AsyncSession) -> None:
    svc = _service(session)
    f = await svc.create(_payload(), ACTOR)
    await svc.update(f.id, FileUpdate(entity_type="candidate", entity_id=7), ACTOR)

    items, total = await svc.list(
        PageParams(page=1, size=20), entity_type="candidate", entity_id=7
    )
    assert total >= 1
    assert all(i.entity_id == 7 for i in items)
