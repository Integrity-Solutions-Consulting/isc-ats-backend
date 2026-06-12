import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.modules.org.api.contacts_schemas import ContactCreate
from app.modules.org.application.contacts_service import (
    ContactCompanyNotFoundError,
    ContactService,
)
from app.modules.org.infrastructure.models import ClientCompany, Contact
from app.shared.repository import BaseRepository

ACTOR = CurrentUser(user_id=1, ip="127.0.0.1")


def _service(session: AsyncSession) -> ContactService:
    return ContactService(
        BaseRepository(session, Contact),
        BaseRepository(session, ClientCompany),
    )


async def test_create_contact_requires_existing_company(session: AsyncSession) -> None:
    data = ContactCreate(
        client_company_id=999999,
        first_name="Ghost",
        last_name="Company",
        email="ghost@nowhere.com",
    )
    with pytest.raises(ContactCompanyNotFoundError):
        await _service(session).create(data, ACTOR)


async def test_create_contact_succeeds_with_real_company(session: AsyncSession) -> None:
    company = await BaseRepository(session, ClientCompany).add(
        ClientCompany(name="Integrity S.A.")
    )
    data = ContactCreate(
        client_company_id=company.id,
        first_name="María",
        last_name="Vélez",
        email="maria.velez@integrity.com.ec",
    )
    contact = await _service(session).create(data, ACTOR)

    assert contact.id is not None
    assert contact.client_company_id == company.id
    assert contact.is_active is True
    assert contact.created_by == ACTOR.user_id
