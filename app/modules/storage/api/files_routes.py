import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile, status
from fastapi import File as FastAPIFile
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.dependencies import CurrentUserDep, SessionDep
from app.core.rate_limit import UPLOAD_LIMIT, limiter
from app.modules.auth.api.authorization import require_permission
from app.modules.storage.api.files_schemas import FileCreate, FileRead, FileUpdate
from app.modules.storage.application.files_service import FileNotFoundError, FileService
from app.modules.storage.application.upload_validation import (
    MAX_UPLOAD_BYTES,
    UploadTooLargeError,
    UploadTypeError,
    validate_upload_bytes,
)
from app.modules.storage.infrastructure.minio_client import minio_client, upload_file_to_minio
from app.modules.storage.infrastructure.models import File
from app.shared.ownership import forbid_candidate_portal, require_owner
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["storage · files"])

# Polymorphic entity_type vocabulary (schema.sql: storage.files.entity_type).
FILE_ENTITY_TYPES = {"cv", "avatar", "vacancy_image", "word_doc"}


def get_service(session: SessionDep) -> FileService:
    return FileService(BaseRepository(session, File))


ServiceDep = Annotated[FileService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[FileRead],
    dependencies=[Depends(require_permission("storage.files.read"))],
)
async def list_files(
    service: ServiceDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    bucket: Annotated[str | None, Query()] = None,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[int | None, Query()] = None,
) -> Page[FileRead]:
    forbid_candidate_portal(current_user)
    params = PageParams(page=page, size=size)
    items, total = await service.list(
        params, bucket=bucket, entity_type=entity_type, entity_id=entity_id
    )
    return Page.create([FileRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{file_id}",
    response_model=FileRead,
    dependencies=[Depends(require_permission("storage.files.read"))],
)
async def get_file(
    file_id: int, service: ServiceDep, current_user: CurrentUserDep
) -> FileRead:
    try:
        file = await service.get(file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if not file.is_public:
        require_owner(current_user, file.created_by)
    return FileRead.model_validate(file)


@router.post(
    "",
    response_model=FileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("storage.files.create"))],
)
async def create_file(
    data: FileCreate, service: ServiceDep, current_user: CurrentUserDep
) -> FileRead:
    return FileRead.model_validate(await service.create(data, current_user))


@router.patch(
    "/{file_id}",
    response_model=FileRead,
    dependencies=[Depends(require_permission("storage.files.update"))],
)
async def update_file(
    file_id: int,
    data: FileUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> FileRead:
    try:
        return FileRead.model_validate(await service.update(file_id, data, current_user))
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("storage.files.delete"))],
)
async def delete_file(file_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{file_id}/download")
async def download_file(
    file_id: int,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> StreamingResponse:
    """Stream a file from MinIO to the client.

    Staff may download anything; candidate-portal tokens only their own
    (or public) files.
    """
    try:
        file = await service.get(file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if not file.is_public:
        require_owner(current_user, file.created_by)

    try:
        obj = minio_client.get_object(file.bucket, file.stored_key)
    except Exception as exc:
        logger.exception("Object storage fetch failed for file %s", file_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error fetching file from storage",
        ) from exc

    def _iter():
        try:
            for chunk in obj.stream(amt=65536):
                yield chunk
        finally:
            obj.close()
            obj.release_conn()

    headers = {"Content-Disposition": f'attachment; filename="{file.original_name}"'}
    if file.size_bytes:
        headers["Content-Length"] = str(file.size_bytes)

    return StreamingResponse(
        _iter(),
        media_type=file.mime_type or "application/octet-stream",
        headers=headers,
    )


@router.post(
    "/upload",
    response_model=FileRead,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(UPLOAD_LIMIT)
async def upload_file(
    request: Request,
    file: Annotated[UploadFile, FastAPIFile(...)],
    service: ServiceDep,
    current_user: CurrentUserDep,
    entity_type: Annotated[str, Form()] = "cv",
    entity_id: Annotated[int | None, Form()] = None,
) -> FileRead:
    """Upload a file to MinIO and record its metadata in storage.files.

    Accessible by any authenticated user (candidates upload their own CVs;
    staff can upload attachments). No extra permission gate — authentication is
    the gate.

    `entity_id` optionally associates the file with a specific entity record
    (e.g. vacancy_id when entity_type is "vacancy_image").
    """
    if entity_type not in FILE_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"entity_type must be one of: {', '.join(sorted(FILE_ENTITY_TYPES))}",
        )

    # Read at most one byte past the cap so an oversized body is rejected without
    # ever materializing the whole payload in memory.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        detected_mime = validate_upload_bytes(entity_type, data)
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except UploadTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        stored_key = upload_file_to_minio(
            file_data=data,
            file_name=file.filename or "upload",
            content_type=detected_mime,
        )
    except Exception as exc:
        logger.exception("Object storage upload failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error uploading to object storage",
        ) from exc

    file_record = FileCreate(
        original_name=file.filename or "upload",
        stored_key=stored_key,
        bucket=settings.minio_bucket,
        mime_type=detected_mime,
        size_bytes=len(data),
        is_public=False,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    created = await service.create(file_record, current_user)
    return FileRead.model_validate(created)
