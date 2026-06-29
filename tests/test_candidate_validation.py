"""Schema-level validation tests for candidate input (mirrors the frontend EC rules).

The frontend validates cédula (EC modulus-10), phone (EC mobile), age >= 18 and
non-empty names. These must be mirrored server-side because the API is the real
source of truth — a raw request bypasses the browser entirely.
"""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.modules.recruitment.api.candidates_schemas import (
    CandidateCreate,
    CandidateUpdate,
)
from app.shared.validators import (
    is_adult,
    is_valid_cedula_ec,
    is_valid_id_number,
    is_valid_phone,
    is_valid_phone_ec,
)

VALID_CEDULA = "1712345675"  # province 17, valid check digit
VALID_PHONE = "0991234567"

BASE = {
    "user_id": 1,
    "first_name": "Juan",
    "last_name": "Pérez",
    "cedula": VALID_CEDULA,
    "phone": VALID_PHONE,
    "birth_date": date(2000, 1, 1),
}


def _create(**override):
    return CandidateCreate(**{**BASE, **override})


# --- shared validators -----------------------------------------------------


def test_valid_cedula_passes():
    assert is_valid_cedula_ec(VALID_CEDULA) is True


@pytest.mark.parametrize(
    "value",
    [
        "1234567890",  # wrong check digit
        "0012345678",  # province 00 (invalid)
        "1762345670",  # third digit >= 6
        "171234567",  # 9 digits
        "abcdefghij",  # non-numeric
    ],
)
def test_invalid_cedula_fails(value):
    assert is_valid_cedula_ec(value) is False


def test_id_number_accepts_passport():
    assert is_valid_id_number("AB123456") is True  # >= 5 chars, not 10 digits


def test_id_number_rejects_short_passport():
    assert is_valid_id_number("AB12") is False


def test_phone_local_and_intl():
    assert is_valid_phone_ec("0991234567") is True
    assert is_valid_phone_ec("+593991234567") is True
    assert is_valid_phone_ec("123") is False
    assert is_valid_phone_ec("0891234567") is False  # must start with 09


def test_is_valid_phone_accepts_ec_and_foreign():
    # Mirrors the frontend validatePhone used by onboarding: EC mobile OR any
    # international E.164. Foreign candidates (common in the EC job market) must
    # not be rejected after the browser already accepted their number.
    assert is_valid_phone("0991234567") is True       # EC local
    assert is_valid_phone("+593991234567") is True     # EC international
    assert is_valid_phone("+12025551234") is True      # foreign E.164
    assert is_valid_phone("+571234567") is True        # foreign E.164
    assert is_valid_phone("123") is False              # too short, no +
    assert is_valid_phone("+12") is False              # below E.164 minimum


def test_is_adult():
    assert is_adult(date(2000, 1, 1)) is True
    minor = date.today().replace(year=date.today().year - 10)
    assert is_adult(minor) is False


# --- CandidateCreate -------------------------------------------------------


def test_valid_candidate_create():
    c = _create()
    assert c.cedula == VALID_CEDULA
    assert c.phone == VALID_PHONE


def test_create_rejects_invalid_cedula():
    with pytest.raises(ValidationError):
        _create(cedula="1234567890")


def test_create_accepts_passport():
    assert _create(cedula="AB123456").cedula == "AB123456"


def test_create_accepts_none_cedula():
    assert _create(cedula=None).cedula is None


def test_create_rejects_invalid_phone():
    with pytest.raises(ValidationError):
        _create(phone="123")


def test_create_accepts_intl_phone():
    assert _create(phone="+593991234567").phone == "+593991234567"


def test_create_accepts_foreign_intl_phone():
    # A foreign E.164 number the onboarding form accepts must not 422 server-side.
    assert _create(phone="+12025551234").phone == "+12025551234"


def test_create_rejects_minor():
    minor = date.today().replace(year=date.today().year - 10)
    with pytest.raises(ValidationError):
        _create(birth_date=minor)


def test_create_rejects_future_birth_date():
    with pytest.raises(ValidationError):
        _create(birth_date=date.today() + timedelta(days=365))


def test_create_rejects_blank_first_name():
    with pytest.raises(ValidationError):
        _create(first_name="   ")


def test_create_trims_names():
    c = _create(first_name="  Juan  ", last_name="  Pérez  ")
    assert c.first_name == "Juan"
    assert c.last_name == "Pérez"


# --- CandidateUpdate (optional fields, validated when present) --------------


def test_update_empty_ok():
    u = CandidateUpdate()
    assert u.phone is None
    assert u.cedula is None


def test_update_rejects_invalid_phone():
    with pytest.raises(ValidationError):
        CandidateUpdate(phone="123")


def test_update_rejects_invalid_cedula():
    with pytest.raises(ValidationError):
        CandidateUpdate(cedula="1234567890")


def test_update_rejects_blank_name():
    with pytest.raises(ValidationError):
        CandidateUpdate(first_name="")


def test_update_accepts_valid_partial():
    u = CandidateUpdate(phone=VALID_PHONE)
    assert u.phone == VALID_PHONE
