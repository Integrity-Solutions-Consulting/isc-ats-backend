from app.core.dependencies import CurrentUser
from app.modules.org.api.contacts_schemas import ContactCreate, ContactUpdate
from app.modules.org.infrastructure.models import ClientCompany, Contact
from app.shared.pagination import PageParams
from app.shared.ports import InUseChecker
from app.shared.repository import BaseRepository


class ContactNotFoundError(Exception):
    pass


class ContactCompanyNotFoundError(Exception):
    """The referenced client_company does not exist (or is inactive)."""


class ContactInUseError(Exception):
    """Cannot delete a contact referenced by a live (non-closed) vacancy."""


class ContactService:
    """Thin CRUD service for org.contacts, validating the client_company FK.

    The DB enforces referential integrity, but the service checks first so the
    API returns a clear error instead of an opaque integrity violation.
    """

    def __init__(
        self,
        repository: BaseRepository[Contact],
        companies: BaseRepository[ClientCompany],
        in_use_checker: InUseChecker | None = None,
    ) -> None:
        self.repository = repository
        self.companies = companies
        self.in_use_checker = in_use_checker

    async def list(
        self, params: PageParams, *, client_company_id: int | None = None
    ) -> tuple[list[Contact], int]:
        filters = {"client_company_id": client_company_id} if client_company_id else None
        return await self.repository.list(params, filters=filters)

    async def get(self, contact_id: int) -> Contact:
        contact = await self.repository.get(contact_id)
        if contact is None:
            raise ContactNotFoundError(f"Contact {contact_id} not found")
        return contact

    async def _assert_company_exists(self, client_company_id: int) -> None:
        if await self.companies.get(client_company_id) is None:
            raise ContactCompanyNotFoundError(
                f"ClientCompany {client_company_id} not found"
            )

    async def create(self, data: ContactCreate, actor: CurrentUser) -> Contact:
        await self._assert_company_exists(data.client_company_id)
        contact = Contact(
            client_company_id=data.client_company_id,
            first_name=data.first_name,
            last_name=data.last_name,
            email=data.email,
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(contact)

    async def update(
        self, contact_id: int, data: ContactUpdate, actor: CurrentUser
    ) -> Contact:
        contact = await self.get(contact_id)
        changes = data.model_dump(exclude_unset=True)
        if "client_company_id" in changes:
            await self._assert_company_exists(changes["client_company_id"])
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(contact, changes)

    async def delete(self, contact_id: int) -> None:
        contact = await self.get(contact_id)
        if self.in_use_checker is not None and await self.in_use_checker(contact_id):
            raise ContactInUseError(
                "No se puede eliminar el contacto: está en uso por una vacante activa."
            )
        await self.repository.soft_delete(contact)
