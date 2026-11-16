from unittest.mock import Mock

import pytest

from app.core.config import settings
from app.modules.ai.application import (
    analysis_service,
    cv_parse_service,
    cv_prefill_service,
    openrouter_client,
)


@pytest.mark.parametrize("service", [analysis_service, cv_parse_service, cv_prefill_service])
def test_omniroute_is_primary_and_falls_back(service, monkeypatch):
    monkeypatch.setattr(settings, "omniroute_base_url", "https://router.example/v1")
    monkeypatch.setattr(settings, "omniroute_api_key", "secret")
    monkeypatch.setattr(settings, "omniroute_model", "combo")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "backup")
    primary = Mock(return_value={"score": 70, "skills": ["Python"]})
    backup = Mock(return_value={"score": 60, "skills": ["Python"]})
    monkeypatch.setattr(service, "call_omniroute", primary)
    monkeypatch.setattr(service, "call_openrouter", backup)
    service._call_gemini(["prompt"])
    backup.assert_not_called()
    primary.side_effect = RuntimeError("unavailable")
    service._call_gemini(["prompt"])
    backup.assert_called_once_with(["prompt"])


def test_omniroute_request_uses_combo_and_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "omniroute_base_url", "https://router.example/v1/")
    monkeypatch.setattr(settings, "omniroute_api_key", "secret")
    monkeypatch.setattr(settings, "omniroute_model", "my-combo")
    response = Mock(is_error=False)
    response.json.return_value = {"choices": [{"message": {"content": '{"score": 70}'}}]}
    client = Mock()
    client.post.return_value = response
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(openrouter_client.httpx, "Client", Mock(return_value=context))
    assert openrouter_client.call_omniroute(["prompt"]) == {"score": 70}
    assert client.post.call_args.args[0] == "https://router.example/v1/chat/completions"
    assert client.post.call_args.kwargs["json"]["model"] == "my-combo"
    assert client.post.call_args.kwargs["json"]["stream"] is False
    assert client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret"
