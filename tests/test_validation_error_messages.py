"""Request-validation failures must reach the user in Spanish.

FastAPI's default RequestValidationError response is a JSON array of Pydantic
error objects whose `msg` is English ("Field required", "Input should be a valid
email address"). The frontend surfaces that text verbatim (see
`lib/backendFetch.ts`), so end users were shown English.

These tests run against a throwaway FastAPI app rather than the real one: the
handler is generic infrastructure and must not depend on a database or on any
particular business endpoint.
"""

from typing import Literal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.validation_errors import (
    register_validation_error_handler,
    validation_error_message,
)


class _Payload(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    age: int
    entity_type: str
    doc_type: Literal["cedula", "passport"] = "cedula"

    @field_validator("age")
    @classmethod
    def _adult(cls, v: int) -> int:
        if v < 18:
            raise ValueError("Debe ser mayor de 18 años")
        return v


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    register_validation_error_handler(application)

    @application.post("/echo")
    async def echo(data: _Payload) -> dict[str, str]:  # pragma: no cover - trivial
        return {"ok": "yes"}

    return application


@pytest.fixture
async def client(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _valid() -> dict[str, object]:
    return {
        "email": "ana@example.com",
        "password": "unaClaveLarga",
        "age": 30,
        "entity_type": "cv",
    }


# --- Response shape -------------------------------------------------------


async def test_detail_is_a_single_string_not_an_array(client: AsyncClient) -> None:
    """The frontend reads `detail` as a string first; an array leaked raw
    Pydantic objects into the UI."""
    res = await client.post("/echo", json={})
    assert res.status_code == 422
    assert isinstance(res.json()["detail"], str)


async def test_valid_payload_still_passes(client: AsyncClient) -> None:
    res = await client.post("/echo", json=_valid())
    assert res.status_code == 200


# --- Message content ------------------------------------------------------


async def test_missing_field_message_is_spanish(client: AsyncClient) -> None:
    payload = _valid()
    del payload["email"]
    res = await client.post("/echo", json=payload)

    detail = res.json()["detail"]
    assert "obligatorio" in detail
    assert "Field required" not in detail


async def test_known_field_uses_a_spanish_label(client: AsyncClient) -> None:
    payload = _valid()
    del payload["password"]
    res = await client.post("/echo", json=payload)

    assert "contraseña" in res.json()["detail"]


async def test_unknown_field_falls_back_to_its_raw_name(client: AsyncClient) -> None:
    """Internal API fields have no Spanish label and must stay debuggable —
    `storage/files/upload` relies on `entity_type` being named."""
    payload = _valid()
    del payload["entity_type"]
    res = await client.post("/echo", json=payload)

    assert "entity_type" in res.json()["detail"]


async def test_invalid_email_message_is_spanish(client: AsyncClient) -> None:
    payload = _valid()
    payload["email"] = "no-es-un-correo"
    res = await client.post("/echo", json=payload)

    detail = res.json()["detail"]
    assert "correo" in detail.lower()
    assert "valid email address" not in detail


async def test_too_short_string_reports_the_minimum(client: AsyncClient) -> None:
    payload = _valid()
    payload["password"] = "corta"
    res = await client.post("/echo", json=payload)

    detail = res.json()["detail"]
    assert "8" in detail
    assert "caracteres" in detail
    assert "should have at least" not in detail


async def test_too_long_string_reports_the_maximum(client: AsyncClient) -> None:
    payload = _valid()
    payload["password"] = "x" * 100
    res = await client.post("/echo", json=payload)

    detail = res.json()["detail"]
    assert "72" in detail
    assert "caracteres" in detail


async def test_non_numeric_value_message_is_spanish(client: AsyncClient) -> None:
    payload = _valid()
    payload["age"] = "treinta"
    res = await client.post("/echo", json=payload)

    detail = res.json()["detail"]
    assert "número" in detail
    assert "valid integer" not in detail


async def test_custom_validator_message_is_preserved(client: AsyncClient) -> None:
    """Schemas in this codebase already raise Spanish ValueErrors. Pydantic
    prefixes them with "Value error, " — the user must not see that."""
    payload = _valid()
    payload["age"] = 15
    res = await client.post("/echo", json=payload)

    detail = res.json()["detail"]
    assert "Debe ser mayor de 18 años" in detail
    assert "Value error" not in detail


async def test_disallowed_literal_message_is_spanish(client: AsyncClient) -> None:
    payload = _valid()
    payload["doc_type"] = "licencia"
    res = await client.post("/echo", json=payload)

    detail = res.json()["detail"]
    assert "no permitido" in detail or "no es válido" in detail
    assert "Input should be" not in detail


async def test_several_errors_are_all_reported(client: AsyncClient) -> None:
    res = await client.post("/echo", json={"email": "x", "password": "s"})

    detail = res.json()["detail"]
    assert "correo" in detail.lower()
    assert "contraseña" in detail


# --- Pure helper ----------------------------------------------------------


def test_message_helper_never_returns_empty_text() -> None:
    """Defensive: an unmapped Pydantic error type must still yield a sentence."""
    message = validation_error_message(
        [{"type": "some_future_type", "loc": ("body", "email"), "msg": "boom"}]
    )
    assert message
    assert "boom" not in message
