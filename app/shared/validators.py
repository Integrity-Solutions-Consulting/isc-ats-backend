"""Ecuador-specific field validators — server-side mirror of the frontend rules.

Kept in lockstep with `isc-ats-frontend/src/shared/utils/ecuadorValidators.ts`.
The API is the source of truth: these run regardless of what the browser did.
"""

import re
from datetime import date

_CEDULA_RE = re.compile(r"^\d{10}$")
_PHONE_LOCAL_RE = re.compile(r"^09\d{8}$")
_PHONE_INTL_RE = re.compile(r"^\+5939\d{8}$")
_CEDULA_COEFFICIENTS = (2, 1, 2, 1, 2, 1, 2, 1, 2)


def is_valid_cedula_ec(value: str) -> bool:
    """Validate an Ecuadorian cédula (10 digits, Registro Civil modulus-10)."""
    if not _CEDULA_RE.match(value):
        return False

    province = int(value[:2])
    if province < 1 or province > 24:
        return False

    if int(value[2]) >= 6:  # only natural persons
        return False

    total = 0
    for i in range(9):
        product = int(value[i]) * _CEDULA_COEFFICIENTS[i]
        if product >= 10:
            product -= 9
        total += product

    remainder = total % 10
    check_digit = 0 if remainder == 0 else 10 - remainder
    return check_digit == int(value[9])


def is_valid_id_number(value: str) -> bool:
    """A 10-digit value must be a valid cédula; otherwise accept it as a passport
    (at least 5 characters). Mirrors the frontend idNumber rule."""
    if re.fullmatch(r"\d{10}", value):
        return is_valid_cedula_ec(value)
    return len(value) >= 5


def is_valid_phone_ec(value: str) -> bool:
    """Accept 09XXXXXXXX (local) or +5939XXXXXXXX (international)."""
    return bool(_PHONE_LOCAL_RE.match(value) or _PHONE_INTL_RE.match(value))


def is_adult(birth_date: date, min_years: int = 18) -> bool:
    """True when `birth_date` is at least `min_years` years before today.
    Future dates are therefore rejected too."""
    today = date.today()
    try:
        cutoff = today.replace(year=today.year - min_years)
    except ValueError:  # Feb 29 on a non-leap cutoff year
        cutoff = today.replace(year=today.year - min_years, day=28)
    return birth_date <= cutoff


PASSWORD_MIN_LENGTH = 10
_PASSWORD_SPECIALS = set("!@#$%^&*()_+-=[]{};:'\",.<>/?\\|`~")


def password_policy_error(value: str) -> str | None:
    """Return a Spanish (Ecuador) error message if the password is weak, else None.

    Policy: at least PASSWORD_MIN_LENGTH characters with at least one lowercase,
    one uppercase, one digit and one special character. Server-side mirror of the
    frontend rule — the API enforces it regardless of what the browser did.
    """
    if len(value) < PASSWORD_MIN_LENGTH:
        return f"La contraseña debe tener al menos {PASSWORD_MIN_LENGTH} caracteres"
    if not any(c.islower() for c in value):
        return "La contraseña debe incluir al menos una letra minúscula"
    if not any(c.isupper() for c in value):
        return "La contraseña debe incluir al menos una letra mayúscula"
    if not any(c.isdigit() for c in value):
        return "La contraseña debe incluir al menos un número"
    if not any(c in _PASSWORD_SPECIALS for c in value):
        return "La contraseña debe incluir al menos un carácter especial"
    return None
