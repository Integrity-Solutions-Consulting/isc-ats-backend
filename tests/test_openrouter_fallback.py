import base64
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import types

from app.core.config import settings
from app.modules.ai.application import (
    analysis_service,
    cv_parse_service,
    cv_prefill_service,
    openrouter_client,
)
from app.modules.ai.application.task_service import failure_policy


@pytest.mark.parametrize("service", [analysis_service, cv_parse_service, cv_prefill_service])
@pytest.mark.parametrize("gemini_key", ["test", ""])
def test_provider_fallback(service, gemini_key, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", gemini_key)
    monkeypatch.setattr(settings, "openrouter_api_key", "backup-test")
    generate = Mock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr(
        service.genai,
        "Client",
        Mock(
            return_value=SimpleNamespace(
                models=SimpleNamespace(generate_content=generate),
            )
        ),
    )
    backup = Mock(return_value={"skills": ["Python"], "score": 77})
    monkeypatch.setattr(service, "call_openrouter", backup)
    result = service._call_gemini(["prompt"])
    assert result.get("score") == 77 or result.get("skills") == ["Python"]
    backup.assert_called_once_with(["prompt"])
    if not gemini_key:
        generate.assert_not_called()


def test_gemini_success_does_not_use_backup(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test")
    monkeypatch.setattr(settings, "openrouter_api_key", "backup-test")
    generate = Mock(return_value=SimpleNamespace(text='{"score": 80}'))
    monkeypatch.setattr(
        analysis_service.genai,
        "Client",
        Mock(
            return_value=SimpleNamespace(
                models=SimpleNamespace(generate_content=generate),
            )
        ),
    )
    backup = Mock()
    monkeypatch.setattr(analysis_service, "call_openrouter", backup)
    assert analysis_service._call_gemini(["prompt"]) == {"score": 80}
    assert generate.call_args.kwargs["model"] == "gemini-3.1-flash-lite"
    backup.assert_not_called()


@pytest.mark.parametrize("status", [200, 401, 429, 503])
def test_openrouter_multimodal_and_failure_policy(status, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "backup-test")
    response = Mock(is_error=status >= 400, status_code=status)
    response.json.return_value = {"choices": [{"message": {"content": '{"score": 70}'}}]}
    client = Mock()
    client.post.return_value = response
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(openrouter_client.httpx, "Client", Mock(return_value=context))
    contents = ["prompt", types.Part.from_bytes(data=b"image", mime_type="image/png")]
    if status == 200:
        assert openrouter_client.call_openrouter(contents) == {"score": 70}
    else:
        with pytest.raises(openrouter_client.OpenRouterError) as caught:
            openrouter_client.call_openrouter(contents)
        assert failure_policy(caught.value, 1)[0] == ("failed" if status == 401 else "pending")
    parts = client.post.call_args.kwargs["json"]["messages"][0]["content"]
    assert (
        parts[1]["image_url"]["url"]
        == "data:image/png;base64," + base64.b64encode(b"image").decode()
    )
