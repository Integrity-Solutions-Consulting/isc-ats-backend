"""Spanish, user-facing messages for request-validation failures.

FastAPI answers a `RequestValidationError` with a JSON array of Pydantic error
objects whose `msg` is English ("Field required", "Input should be a valid
integer"). The frontend proxy surfaces that text verbatim — `extractDetail` in
`lib/backendFetch.ts` reads `detail[0].msg` — so end users were shown English
technical strings.

This handler collapses the array into a single Spanish sentence under a string
`detail`, which is the shape the frontend already prefers. The HTTP status stays
422, and the machine-readable errors are still logged for debugging.

Only Pydantic's automatic messages are translated. A schema that raises its own
`ValueError` (several already do, in Spanish) keeps its wording.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status

logger = logging.getLogger(__name__)

# How many problems to state at once. Beyond this the message becomes a wall of
# text that nobody reads; the form highlights the rest field by field anyway.
_MAX_REPORTED = 3

# Request "sections" Pydantic prefixes `loc` with — never a field name.
_LOC_SECTIONS = frozenset({"body", "query", "path", "header", "cookie"})

# Fields a real person fills in a form get a Spanish label. Anything absent here
# is an internal API field (e.g. entity_type), and falls back to its raw name so
# the message stays debuggable instead of vaguely wrong.
_FIELD_LABELS: dict[str, str] = {
    "email": "correo",
    "password": "contraseña",
    "current_password": "contraseña actual",
    "new_password": "nueva contraseña",
    "first_name": "nombres",
    "last_name": "apellidos",
    "cedula": "cédula",
    "id_number": "documento de identidad",
    "doc_type": "tipo de documento",
    "phone": "teléfono",
    "birth_date": "fecha de nacimiento",
    "home_address": "dirección",
    "city": "ciudad",
    "career": "carrera",
    "title": "título",
    "education_level": "nivel de educación",
    "name": "nombre",
    "description": "descripción",
    "content": "contenido",
    "vacancy_name": "nombre de la vacante",
    "position": "cargo",
    "openings": "número de vacantes",
    "work_mode": "modalidad de trabajo",
    "work_schedule": "horario de trabajo",
    "salary": "salario",
    "experience_years": "años de experiencia",
    "start_time": "hora de inicio",
    "end_time": "hora de fin",
    "scheduled_at": "fecha y hora",
    "rejection_reason": "motivo del rechazo",
    "role_id": "rol",
    "client_company_id": "cliente",
    "department_id": "departamento",
    "process_id": "proceso",
}


def _field_label(loc: tuple[Any, ...]) -> str | None:
    """Human label for the field a Pydantic error points at.

    `loc` looks like ("body", "email") or ("body", "items", 0, "name"). The last
    string that is not a request section is the field; a list index is not.
    """
    for part in reversed(loc):
        if isinstance(part, str) and part not in _LOC_SECTIONS:
            return _FIELD_LABELS.get(part, part)
    return None


def _named(label: str | None) -> str:
    """Refer to the field by name, or generically when there is no field."""
    return f'El campo "{label}"' if label else "La información enviada"


def _message_for(error: dict[str, Any]) -> str:
    """One Spanish sentence for a single Pydantic error object."""
    error_type = str(error.get("type", ""))
    ctx: dict[str, Any] = error.get("ctx") or {}
    label = _field_label(tuple(error.get("loc") or ()))
    named = _named(label)

    # A schema raised its own ValueError — this codebase writes those in Spanish
    # already, so keep the author's wording and drop Pydantic's "Value error, "
    # prefix (which lives in `msg`, not in `ctx`).
    custom = ctx.get("error")
    if error_type == "value_error" and custom is not None:
        text = str(custom).strip()
        if text:
            return text if text.endswith((".", "!", "?")) else f"{text}."

    if error_type == "missing":
        return f"{named} es obligatorio."

    if error_type == "string_too_short":
        minimum = ctx.get("min_length")
        if minimum == 1:
            return f"{named} no puede estar vacío."
        return f"{named} debe tener al menos {minimum} caracteres."

    if error_type == "string_too_long":
        return f"{named} no puede superar los {ctx.get('max_length')} caracteres."

    if error_type in {"int_parsing", "int_type", "float_parsing", "float_type", "decimal_parsing"}:
        return f"{named} debe ser un número."

    if error_type in {"greater_than", "greater_than_equal"}:
        return f"{named} debe ser mayor o igual a {ctx.get('gt', ctx.get('ge'))}."

    if error_type in {"less_than", "less_than_equal"}:
        return f"{named} debe ser menor o igual a {ctx.get('lt', ctx.get('le'))}."

    if error_type in {"bool_parsing", "bool_type"}:
        return f"{named} debe ser verdadero o falso."

    if error_type in {
        "date_parsing",
        "date_type",
        "datetime_parsing",
        "datetime_type",
        "time_parsing",
        "time_type",
    }:
        return f"{named} debe ser una fecha válida."

    if error_type in {"literal_error", "enum"}:
        return f"{named} tiene un valor no permitido."

    if error_type in {"too_short", "too_long", "list_type"}:
        return f"{named} no tiene la cantidad de elementos esperada."

    if error_type == "json_invalid":
        return "La información enviada no tiene un formato válido."

    # EmailStr failures arrive as a plain value_error with no ctx["error"].
    if "email" in error_type or "email address" in str(error.get("msg", "")):
        return "Ingresa un correo electrónico válido."

    return f"{named} no es válido."


def validation_error_message(errors: list[dict[str, Any]]) -> str:
    """Collapse Pydantic's error list into one Spanish sentence for the user."""
    messages: list[str] = []
    for error in errors[:_MAX_REPORTED]:
        message = _message_for(error)
        if message not in messages:
            messages.append(message)
    return " ".join(messages) or "La información enviada no es válida."


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Answer 422 with a Spanish `detail` string instead of raw Pydantic errors."""
    errors = exc.errors()
    # The structured errors never reach the client now, so keep them in the log —
    # otherwise a malformed request becomes undiagnosable.
    logger.info("Request validation failed on %s %s: %s", request.method, request.url.path, errors)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": validation_error_message(errors)},
    )


def register_validation_error_handler(app: FastAPI) -> None:
    """Wire the Spanish validation-error handler onto an app."""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
