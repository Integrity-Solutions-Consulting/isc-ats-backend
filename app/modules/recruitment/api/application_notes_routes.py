from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.auth.infrastructure.models import User
from app.modules.recruitment.api.application_notes_schemas import (
    ApplicationNoteCreate,
    ApplicationNoteRead,
    ApplicationNoteUpdate,
)
from app.modules.recruitment.application.application_notes_service import (
    ApplicationNoteNotFoundError,
    ApplicationNoteReferenceError,
    ApplicationNoteService,
)
from app.modules.recruitment.infrastructure.application_models import (
    Application,
    ApplicationNote,
)
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/application-notes", tags=["recruitment · application notes"])


def get_service(session: SessionDep) -> ApplicationNoteService:
    return ApplicationNoteService(
        BaseRepository(session, ApplicationNote),
        BaseRepository(session, Application),
        users=BaseRepository(session, User),
    )


ServiceDep = Annotated[ApplicationNoteService, Depends(get_service)]
_READ = Depends(require_permission("recruitment.application_notes.read"))


@router.get("", response_model=Page[ApplicationNoteRead], dependencies=[_READ])
async def list_notes(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    application_id: Annotated[int | None, Query()] = None,
) -> Page[ApplicationNoteRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, application_id=application_id)
    enriched = [await service._enrich_author(i) for i in items]
    return Page.create(enriched, total, params)


@router.get("/{note_id}", response_model=ApplicationNoteRead, dependencies=[_READ])
async def get_note(note_id: int, service: ServiceDep) -> ApplicationNoteRead:
    try:
        note = await service.get(note_id)
        return await service._enrich_author(note)
    except ApplicationNoteNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ApplicationNoteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.application_notes.create"))],
)
async def create_note(
    data: ApplicationNoteCreate, service: ServiceDep, current_user: CurrentUserDep
) -> ApplicationNoteRead:
    try:
        created = await service.create(data, current_user)
    except ApplicationNoteReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return await service._enrich_author(created)


@router.patch(
    "/{note_id}",
    response_model=ApplicationNoteRead,
    dependencies=[Depends(require_permission("recruitment.application_notes.update"))],
)
async def update_note(
    note_id: int,
    data: ApplicationNoteUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> ApplicationNoteRead:
    try:
        updated = await service.update(note_id, data, current_user)
    except ApplicationNoteNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return await service._enrich_author(updated)


@router.delete(
    "/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("recruitment.application_notes.delete"))],
)
async def delete_note(note_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(note_id)
    except ApplicationNoteNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
