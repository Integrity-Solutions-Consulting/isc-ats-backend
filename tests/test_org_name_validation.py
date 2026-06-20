"""Schema-level tests: org catalog name fields must reject blank/whitespace input.

Mirrors the frontend's required-name rule for departments, clients, parameters
and contacts so a raw API request can't persist an empty name.
"""

import pytest
from pydantic import ValidationError

from app.modules.org.api.client_companies_schemas import (
    ClientCompanyCreate,
    ClientCompanyUpdate,
)
from app.modules.org.api.contacts_schemas import ContactCreate
from app.modules.org.api.departments_schemas import (
    DepartmentCreate,
    DepartmentUpdate,
)
from app.modules.org.api.parameters_schemas import ParameterCreate


def test_department_blank_name_rejected():
    with pytest.raises(ValidationError):
        DepartmentCreate(name="   ")


def test_department_name_trimmed():
    assert DepartmentCreate(name="  Tecnología  ").name == "Tecnología"


def test_department_update_blank_rejected():
    with pytest.raises(ValidationError):
        DepartmentUpdate(name="")


def test_department_update_none_ok():
    assert DepartmentUpdate().name is None


def test_client_blank_name_rejected():
    with pytest.raises(ValidationError):
        ClientCompanyCreate(name="  ")


def test_client_update_blank_rejected():
    with pytest.raises(ValidationError):
        ClientCompanyUpdate(name="   ")


def test_parameter_blank_name_rejected():
    with pytest.raises(ValidationError):
        ParameterCreate(type="city", code="quito", name="  ")


def test_contact_blank_first_name_rejected():
    with pytest.raises(ValidationError):
        ContactCreate(
            client_company_id=1, first_name="  ", last_name="Vélez", email="a@b.com"
        )


def test_contact_valid_trimmed():
    c = ContactCreate(
        client_company_id=1, first_name=" María ", last_name=" Vélez ", email="maria@e.com"
    )
    assert c.first_name == "María"
    assert c.last_name == "Vélez"
