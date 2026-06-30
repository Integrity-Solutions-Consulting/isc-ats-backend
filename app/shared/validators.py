"""Ecuador-specific field validators — server-side mirror of the frontend rules.

Kept in lockstep with `isc-ats-frontend/src/shared/utils/ecuadorValidators.ts`.
The API is the source of truth: these run regardless of what the browser did.
"""

import re
from datetime import date

_CEDULA_RE = re.compile(r"^\d{10}$")
_PASSPORT_RE = re.compile(r"^[A-Z0-9]{6,20}$", re.IGNORECASE)
_PHONE_LOCAL_RE = re.compile(r"^09\d{8}$")
_PHONE_INTL_RE = re.compile(r"^\+5939\d{8}$")
_PHONE_E164_RE = re.compile(r"^\+\d{7,15}$")
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
    (at least 5 characters). Mirrors the frontend idNumber rule.

    Used as the fallback when the document type is unknown (e.g. a partial update
    that does not send doc_type). When the type IS known, validate with
    is_valid_cedula_ec or is_valid_passport directly."""
    if re.fullmatch(r"\d{10}", value):
        return is_valid_cedula_ec(value)
    return len(value) >= 5


def is_valid_passport(value: str) -> bool:
    """Validate an international passport: 6–20 alphanumeric characters.
    Mirrors the frontend validatePassport."""
    return bool(_PASSPORT_RE.match(value))


def is_valid_phone_ec(value: str) -> bool:
    """Accept 09XXXXXXXX (local) or +5939XXXXXXXX (international)."""
    return bool(_PHONE_LOCAL_RE.match(value) or _PHONE_INTL_RE.match(value))


def is_valid_phone(value: str) -> bool:
    """Accept an Ecuadorian mobile or any international E.164 number (+ and 7–15
    digits). Mirrors the frontend `validatePhone` used by the onboarding form, so
    a foreign candidate's number that the browser accepts is not rejected by the
    API. Use this for candidate input; `is_valid_phone_ec` stays EC-strict."""
    return is_valid_phone_ec(value) or bool(_PHONE_E164_RE.match(value))


def is_adult(birth_date: date, min_years: int = 18) -> bool:
    """True when `birth_date` is at least `min_years` years before today.
    Future dates are therefore rejected too."""
    today = date.today()
    try:
        cutoff = today.replace(year=today.year - min_years)
    except ValueError:  # Feb 29 on a non-leap cutoff year
        cutoff = today.replace(year=today.year - min_years, day=28)
    return birth_date <= cutoff


def is_within_max_age(birth_date: date, max_years: int = 65) -> bool:
    """True when `birth_date` is at most `max_years` years before today, i.e. the
    person is no older than the maximum allowed age. Mirrors the frontend maxAge,
    so a date the browser accepts is not rejected by the API."""
    today = date.today()
    try:
        cutoff = today.replace(year=today.year - max_years)
    except ValueError:  # Feb 29 on a non-leap cutoff year
        cutoff = today.replace(year=today.year - max_years, day=28)
    return birth_date >= cutoff


PASSWORD_MIN_LENGTH = 8
# A "special" character is any non-alphanumeric one — mirrors the frontend rule
# (/[^a-zA-Z0-9]/). A fixed allowlist would reject chars common on EC/Spanish
# keyboards (¿ ¡ €) that the browser already accepted, breaking registration.
_PASSWORD_SPECIAL_RE = re.compile(r"[^a-zA-Z0-9]")


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
    if not _PASSWORD_SPECIAL_RE.search(value):
        return "La contraseña debe incluir al menos un carácter especial"
    return None
