from app.core.dependencies import CurrentUser
from app.modules.org.api.client_companies_schemas import (
    ClientCompanyCreate,
    ClientCompanyUpdate,
)
from app.modules.org.infrastructure.models import ClientCompany
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class ClientCompanyNotFoundError(Exception):
    pass


class ClientCompanyService:
    """Thin CRUD service for the org.client_companies catalog."""

    def __init__(self, repository: BaseRepository[ClientCompany]) -> None:
        self.repository = repository

    async def list(self, params: PageParams) -> tuple[list[ClientCompany], int]:
        return await self.repository.list(params)

    async def get(self, company_id: int) -> ClientCompany:
        company = await self.repository.get(company_id)
        if company is None:
            raise ClientCompanyNotFoundError(f"ClientCompany {company_id} not found")
        return company

    async def create(self, data: ClientCompanyCreate, actor: CurrentUser) -> ClientCompany:
        company = ClientCompany(
            name=data.name,
            legal_name=data.legal_name,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(company)

    async def update(
        self, company_id: int, data: ClientCompanyUpdate, actor: CurrentUser
    ) -> ClientCompany:
        company = await self.get(company_id)
        changes = data.model_dump(exclude_unset=True)
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(company, changes)

    async def delete(self, company_id: int) -> None:
        company = await self.get(company_id)
        await self.repository.soft_delete(company)
