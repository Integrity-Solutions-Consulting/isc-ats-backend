"""Tests for interview_status and interview_scheduler seed parameters.

These are integration tests that verify the seed migration loaded the expected
parameters into org.parameters. The test rolls back — the rows must already
exist in the DB (applied via alembic upgrade head) for the assertions to pass.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.org.infrastructure.parameters_repository import ParameterRepository


@pytest.mark.parametrize(
    "type_,code,expected_name",
    [
        ("interview_status", "scheduled", "Agendada"),
        ("interview_status", "offered", "Ofrecida"),
        ("interview_status", "confirmed", "Confirmada"),
        ("interview_status", "cancelled", "Cancelada"),
        ("interview_status", "completed", "Completada"),
        ("interview_scheduler", "hr", "RH"),
        ("interview_scheduler", "candidate", "Candidato"),
    ],
)
async def test_interview_param_exists(
    session: AsyncSession, type_: str, code: str, expected_name: str
) -> None:
    """Each interview_status / interview_scheduler param must be present and active."""
    param = await ParameterRepository(session).get_by_type_and_code(type_, code)
    assert param is not None, f"Parameter ({type_}, {code}) not found in org.parameters"
    assert param.name == expected_name, f"Expected name '{expected_name}', got '{param.name}'"
    assert param.is_active is True
