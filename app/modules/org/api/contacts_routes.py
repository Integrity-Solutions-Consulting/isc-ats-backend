from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.api.contacts_schemas import (
    ContactCreate,
    ContactRead,
    ContactUpdate,
)
from app.modules.org.application.contacts_service import (
    ContactCompanyNotFoundError,
    ContactInUseError,
    ContactNotFoundError,
    ContactService,
)
from app.modules.org.infrastructure.models import ClientCompany, Contact
from app.modules.recruitment.infrastructure.vacancy_usage_repository import (
    VacancyUsageRepository,
)
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/contacts", tags=["org · contacts"])


def get_service(session: SessionDep) -> ContactService:
    usage = VacancyUsageRepository(session)
    return ContactService(
        BaseRepository(session, Contact),
        BaseRepository(session, ClientCompany),
        in_use_checker=lambda cid: usage.is_referenced_by_live_vacancy("contact_id", cid),
    )


ServiceDep = Annotated[ContactService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[ContactRead],
    dependencies=[Depends(require_permission("org.contacts.read"))],
)
async def list_contacts(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    client_company_id: Annotated[int | None, Query(description="Filter by company")] = None,
) -> Page[ContactRead]:
    params = PageParams(page=page, size=size)
    items, total = await service.list(params, client_company_id=client_company_id)
    return Page.create([ContactRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{contact_id}",
    response_model=ContactRead,
    dependencies=[Depends(require_permission("org.contacts.read"))],
)
async def get_contact(contact_id: int, service: ServiceDep) -> ContactRead:
    try:
        return ContactRead.model_validate(await service.get(contact_id))
    except ContactNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ContactRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("org.contacts.create"))],
)
async def create_contact(
    data: ContactCreate, service: ServiceDep, current_user: CurrentUserDep
) -> ContactRead:
    try:
        created = await service.create(data, current_user)
    except ContactCompanyNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ContactRead.model_validate(created)


@router.patch(
    "/{contact_id}",
    response_model=ContactRead,
    dependencies=[Depends(require_permission("org.contacts.update"))],
)
async def update_contact(
    contact_id: int,
    data: ContactUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> ContactRead:
    try:
        updated = await service.update(contact_id, data, current_user)
    except ContactNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ContactCompanyNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ContactRead.model_validate(updated)


@router.delete(
    "/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("org.contacts.delete"))],
)
async def delete_contact(contact_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(contact_id)
    except ContactNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ContactInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
