import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.core.config import settings
from app.core.structured_logging import JsonFormatter
from app.modules.ai.application import analysis_service, openrouter_client
from app.modules.ai.infrastructure import usage_tracking


class ModelError(Exception):
    code = 503


@pytest.mark.asyncio
async def test_gemini_attempts_are_saved_with_context(monkeypatch):
    saved = []

    async def persist(values):
        saved.append(values)

    monkeypatch.setattr(usage_tracking, "persist_usage", persist)
    monkeypatch.setattr(settings, "gemini_api_key", "test")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(analysis_service, "_FALLBACK_MODELS", ["first", "second"])
    response = SimpleNamespace(
        text='{"score": 75}',
        usage_metadata=SimpleNamespace(prompt_token_count=42, candidates_token_count=8),
    )
    client = Mock()
    client.models.generate_content.side_effect = [ModelError(), response]
    monkeypatch.setattr(analysis_service.genai, "Client", lambda **kwargs: client)
    result = await usage_tracking.tracked_to_thread(
        analysis_service._call_gemini, ["private CV"], action="analyze_application",
        task_id=10, entity_id=20,
    )
    assert result == {"score": 75}
    assert [(x["model"], x["status"], x["error_code"]) for x in saved] == [
        ("first", "error", "503"), ("second", "success", None),
    ]
    assert saved[1]["input_tokens"] == 42 and saved[1]["output_tokens"] == 8
    assert all(x["task_id"] == 10 and x["entity_id"] == 20 for x in saved)
    assert "private CV" not in repr(saved)


@pytest.mark.asyncio
async def test_openrouter_usage_and_failure_are_recorded(monkeypatch):
    saved = []

    async def persist(values):
        saved.append(values)

    monkeypatch.setattr(usage_tracking, "persist_usage", persist)
    monkeypatch.setattr(settings, "openrouter_api_key", "test")
    response = Mock(is_error=False)
    response.json.return_value = {
        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        "choices": [{"message": {"content": '{"ok": true}'}}],
    }
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response
    monkeypatch.setattr(openrouter_client.httpx, "Client", lambda **kwargs: client)
    result = await usage_tracking.tracked_to_thread(
        openrouter_client.call_openrouter, ["private CV"], action="cv_prefill"
    )
    assert result == {"ok": True}
    assert saved[0]["input_tokens"] == 11 and saved[0]["status"] == "success"
    response.is_error = True
    response.status_code = 403
    with pytest.raises(openrouter_client.OpenRouterError):
        await usage_tracking.tracked_to_thread(
            openrouter_client.call_openrouter, ["private CV"], action="cv_prefill"
        )
    assert saved[1]["status"] == "error" and saved[1]["error_code"] == "403"


@pytest.mark.asyncio
async def test_db_logging_failure_does_not_break_ai(monkeypatch):
    monkeypatch.setattr(
        usage_tracking, "persist_usage", AsyncMock(side_effect=RuntimeError("down"))
    )

    def execute():
        with usage_tracking.track_attempt("model"):
            return {"ok": True}

    assert await usage_tracking.tracked_to_thread(execute, action="test") == {"ok": True}


def test_worker_json_log_has_context_without_exception_body():
    import logging

    record = logging.LogRecord("app.worker", logging.ERROR, "", 1, "failed", (), None)
    record.task_id = 9
    data = json.loads(JsonFormatter().format(record))
    assert data["task_id"] == 9 and data["level"] == "ERROR"


@pytest.mark.asyncio
async def test_task_error_before_provider_is_recorded(monkeypatch):
    from app.modules.ai.application import cv_parse_service, task_service

    task = SimpleNamespace(
        id=1, lease_token="lease", status="queued", attempts=0,
        action="parse_candidate_cv", entity_id=2, file_id=3,
    )
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.scalar = AsyncMock(return_value=task)
    session.commit = AsyncMock()
    monkeypatch.setattr(task_service, "async_session_factory", lambda: session)
    monkeypatch.setattr(
        cv_parse_service, "parse_candidate_cv", AsyncMock(side_effect=ValueError("bad input"))
    )
    persist = AsyncMock()
    monkeypatch.setattr(task_service, "persist_usage", persist)
    await task_service.run_ai_task(1, "lease")
    assert task.status == "failed"
    assert persist.await_args.args[0] == {
        "action": "parse_candidate_cv:task", "status": "error", "error_code": "ValueError",
        "task_id": 1, "entity_id": 2,
    }
