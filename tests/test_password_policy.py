"""Password strength policy (server-side enforcement).

Unit-tests the policy helper and the schema validators that gate registration
and password changes. Mirrors isc-ats-frontend's password rule.
"""

import pytest
from pydantic import ValidationError

from app.modules.auth.api.auth_schemas import ChangePasswordRequest, RegisterRequest
from app.shared.validators import password_policy_error

_STRONG = "StrongPass123!"


@pytest.mark.parametrize(
    "weak",
    [
        "Short1!",          # too short (< 10)
        "alllowercase1!",   # no uppercase
        "ALLUPPERCASE1!",   # no lowercase
        "NoDigitsHere!",    # no digit
        "NoSpecial1234",    # no special char
        "password123",      # the report's example — no upper, no special
        "Test123456",       # the report's example — no special char
    ],
)
def test_policy_rejects_weak_passwords(weak: str) -> None:
    assert password_policy_error(weak) is not None


def test_policy_accepts_strong_password() -> None:
    assert password_policy_error(_STRONG) is None


def test_register_schema_rejects_weak_password() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="password123")


def test_register_schema_accepts_strong_password() -> None:
    model = RegisterRequest(email="a@b.com", password=_STRONG)
    assert model.password == _STRONG


def test_change_password_schema_rejects_weak_new_password() -> None:
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="whatever", new_password="weak")
