from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import settings
from app.core.dependencies import CurrentUserDep, SessionDep
from app.modules.auth.api.authorization import require_permission
from app.modules.org.api.client_companies_schemas import (
    ClientCompanyCreate,
    ClientCompanyRead,
    ClientCompanyUpdate,
)
from app.modules.org.application.client_companies_service import (
    ClientCompanyInUseError,
    ClientCompanyNotFoundError,
    ClientCompanyService,
)
from app.modules.org.application.client_sync_service import get_client_sync_service
from app.modules.org.infrastructure.models import ClientCompany
from app.modules.org.infrastructure.org_usage_repository import OrgUsageRepository
from app.modules.recruitment.infrastructure.vacancy_usage_repository import (
    VacancyUsageRepository,
)
from app.shared.pagination import Page, PageParams
from app.shared.repository import BaseRepository

router = APIRouter(prefix="/client-companies", tags=["org · client companies"])


def get_service(session: SessionDep) -> ClientCompanyService:
    usage = VacancyUsageRepository(session)
    org = OrgUsageRepository(session)

    async def in_use(company_id: int) -> bool:
        return (
            await usage.is_referenced_by_live_vacancy("client_company_id", company_id)
            or await org.has_active_contacts_for_company(company_id)
            or await org.has_active_processes_for_company(company_id)
        )

    return ClientCompanyService(BaseRepository(session, ClientCompany), in_use_checker=in_use)


ServiceDep = Annotated[ClientCompanyService, Depends(get_service)]


@router.get(
    "",
    response_model=Page[ClientCompanyRead],
    dependencies=[Depends(require_permission("org.client_companies.read"))],
)
async def list_client_companies(
    service: ServiceDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[ClientCompanyRead]:
    # Sync-on-read: refresh the TMR mirror before listing (throttled + fail-safe).
    # The request's session commits on success (get_session), so upserts persist.
    # When TMR is enabled the dropdown shows only TMR-sourced rows (external_id set);
    # when disabled, behaviour is unchanged (all rows) so local dev isn't broken.
    if settings.tmr_enabled:
        await get_client_sync_service().sync(session)
    params = PageParams(page=page, size=size)
    items, total = await service.list(
        params, include_inactive=include_inactive, external_only=settings.tmr_enabled
    )
    return Page.create([ClientCompanyRead.model_validate(i) for i in items], total, params)


@router.get(
    "/{company_id}",
    response_model=ClientCompanyRead,
    dependencies=[Depends(require_permission("org.client_companies.read"))],
)
async def get_client_company(company_id: int, service: ServiceDep) -> ClientCompanyRead:
    try:
        return ClientCompanyRead.model_validate(await service.get(company_id))
    except ClientCompanyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=ClientCompanyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("org.client_companies.create"))],
)
async def create_client_company(
    data: ClientCompanyCreate, service: ServiceDep, current_user: CurrentUserDep
) -> ClientCompanyRead:
    return ClientCompanyRead.model_validate(await service.create(data, current_user))


@router.patch(
    "/{company_id}",
    response_model=ClientCompanyRead,
    dependencies=[Depends(require_permission("org.client_companies.update"))],
)
async def update_client_company(
    company_id: int,
    data: ClientCompanyUpdate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> ClientCompanyRead:
    try:
        updated = await service.update(company_id, data, current_user)
    except ClientCompanyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return ClientCompanyRead.model_validate(updated)


@router.delete(
    "/{company_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("org.client_companies.delete"))],
)
async def delete_client_company(company_id: int, service: ServiceDep) -> None:
    try:
        await service.delete(company_id)
    except ClientCompanyNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ClientCompanyInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
