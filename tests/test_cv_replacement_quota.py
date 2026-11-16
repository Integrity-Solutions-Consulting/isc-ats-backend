from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.dependencies import CurrentUser
from app.modules.recruitment.api import candidates_routes
from app.modules.recruitment.api.candidates_schemas import CandidateUpdate
from app.modules.storage.application.files_service import FileService


@pytest.mark.asyncio
async def test_quota_reserves_space_for_current_cv_but_counts_other_uploads():
    session = SimpleNamespace(execute=AsyncMock())
    session.execute.return_value = Mock()
    session.execute.return_value.one.return_value = (9, 9000)
    service = FileService(SimpleNamespace(session=session))
    assert not await service.cv_quota_exceeded(7, 1000, max_count=10, max_total_bytes=50000)
    query = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "recruitment.candidates" in query and "cv_file_id" in query
    session.execute.return_value.one.return_value = (10, 10000)
    assert await service.cv_quota_exceeded(7, 1000, max_count=10, max_total_bytes=50000)


@pytest.mark.asyncio
@pytest.mark.parametrize("other_reference,expected_active", [(None, False), (42, True)])
async def test_replacing_cv_retires_only_if_unshared(monkeypatch, other_reference, expected_active):
    current = SimpleNamespace(user_id=7, cv_file_id=1, parsed_data={"skills": ["SQL"]})
    old_file = SimpleNamespace(entity_type="cv", is_active=True)
    service = SimpleNamespace(
        get=AsyncMock(return_value=current), update=AsyncMock(return_value=current)
    )
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        scalar=AsyncMock(side_effect=[other_reference, None]),
        get=AsyncMock(return_value=old_file),
    )
    monkeypatch.setattr(candidates_routes, "schedule", AsyncMock())
    monkeypatch.setattr(candidates_routes.CandidateRead, "model_validate", Mock(return_value="ok"))
    result = await candidates_routes.update_candidate(
        5, CandidateUpdate(cv_file_id=2), service, session,
        CurrentUser(user_id=7, ip="127.0.0.1", portal="candidate"), None,
    )
    assert result == "ok"
    assert old_file.is_active is expected_active
    assert current.parsed_data is None


@pytest.mark.asyncio
async def test_failed_replacement_does_not_retire_old_cv():
    current = SimpleNamespace(user_id=7, cv_file_id=1)
    service = SimpleNamespace(
        get=AsyncMock(return_value=current),
        update=AsyncMock(side_effect=candidates_routes.CandidateReferenceError("invalid")),
    )
    session = SimpleNamespace(get=AsyncMock())
    with pytest.raises(candidates_routes.HTTPException) as error:
        await candidates_routes.update_candidate(
            5, CandidateUpdate(cv_file_id=2), service, session,
            CurrentUser(user_id=7, ip=None, portal="candidate"), None,
        )
    assert error.value.status_code == 422
    session.get.assert_not_awaited()
