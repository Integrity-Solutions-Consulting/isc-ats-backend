from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.api.application_documents_schemas import (
    ApplicationDocumentCreate,
    ApplicationDocumentRead,
    ApplicationDocumentUpdate,
)
from app.modules.recruitment.application.application_documents_service import (
    ApplicationDocumentNotFoundError,
    ApplicationDocumentReferenceError,
    ApplicationDocumentService,
)
from app.modules.recruitment.infrastructure.application_models import (
    Application,
    ApplicationDocument,
)
from app.modules.storage.infrastructure.models import File
from app.shared.ownership import forbid_candidate_portal
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(
    prefix="/application-documents", tags=["recruitment · application documents"]
)


def get_service(session: SessionDep) -> ApplicationDocumentService:
    return ApplicationDocumentService(
        BaseRepository(session, ApplicationDocument),
        BaseRepository(session, Application),
        BaseRepository(session, File),
        BaseRepository(session, Parameter),
    )


ServiceDep = Annotated[ApplicationDocumentService, Depends(get_service)]
_READ = Depends(require_permission("recruitment.application_documents.read"))


@router.get("", response_model=Page[ApplicationDocumentRead], dependencies=[_READ])
async def list_documents(
    service: ServiceDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    application_id: Annotated[int | None, Query()] = None,
) -> Page[ApplicationDocumentRead]:
    # Staff-only listing: these documents are not row-scoped per candidate, so a
    # candidate-portal token must never reach them even if granted the permission.
    forbid_candidate_portal(current_user)
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, application_id=application_id)
    return Page.create(
        [ApplicationDocumentRead.model_validate(i) for i in items], total, params
    )


@router.get("/{document_id}", response_model=ApplicationDocumentRead, dependencies=[_READ])
async def get_document(
    document_id: int, service: ServiceDep, current_user: CurrentUserDep
) -> ApplicationDocumentRead:
    forbid_candidate_portal(current_user)
    try:
        return ApplicationDocumentRead.model_validate(await service.get(document_id))
    except ApplicationDocumentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ApplicationDocumentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("recruitment.application_documents.create"))],
)
async def create_document(
    data: ApplicationDocumentCreate, service: ServiceDep, current_user: CurrentUserDep
) -> ApplicationDocumentRead:
    try:
        created = await service.create(data, current_user)
    except ApplicationDocumentReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ApplicationDocumentRead.model_validate(created)


@router.patch(
    "/{document_id}",
    response_model=ApplicationDocumentRead,
    dependencies=[Depends(require_permission("recruitment.application_documents.update"))],
)
async def update_document(
    document_id: int,
    data: ApplicationDocumentUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> ApplicationDocumentRead:
    try:
        updated = await service.update(document_id, data, current_user)
    except ApplicationDocumentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ApplicationDocumentReferenceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ApplicationDocumentRead.model_validate(updated)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("recruitment.application_documents.delete"))],
)
async def delete_document(document_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(document_id)
    except ApplicationDocumentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
