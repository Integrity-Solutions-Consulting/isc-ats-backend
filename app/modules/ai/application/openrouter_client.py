"""OpenRouter backup for the existing text and CV page-image prompts."""

import base64
import json
from typing import Any

import httpx

from app.core.config import settings
from app.modules.ai.infrastructure.usage_tracking import Attempt, track_attempt


class OpenRouterError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"OpenRouter request failed (HTTP {status_code})")


def call_openrouter(contents: list[Any]) -> dict:
    if not settings.openrouter_api_key:
        raise ValueError("OpenRouter is not configured")
    with track_attempt(settings.openrouter_model, provider="openrouter") as attempt:
        return _call_openrouter(contents, attempt)


def call_omniroute(contents: list[Any]) -> dict:
    if not settings.omniroute_configured:
        raise ValueError("OmniRoute is not configured")
    with track_attempt(settings.omniroute_model, provider="omniroute") as attempt:
        return _call_openrouter(
            contents, attempt,
            url=f"{settings.omniroute_base_url.rstrip('/')}/chat/completions",
            api_key=settings.omniroute_api_key,
            model=settings.omniroute_model,
        )


def _call_openrouter(
    contents: list[Any],
    attempt: Attempt,
    *,
    url: str = "https://openrouter.ai/api/v1/chat/completions",
    api_key: str | None = None,
    model: str | None = None,
) -> dict:
    parts = []
    for part in contents:
        if isinstance(part, str):
            parts.append({"type": "text", "text": part})
        elif getattr(part, "inline_data", None) is not None:
            blob = part.inline_data
            if not blob.mime_type.startswith("image/"):
                raise ValueError("Unsupported OpenRouter input type")
            encoded = base64.b64encode(blob.data).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{blob.mime_type};base64,{encoded}",
                    },
                }
            )
        else:
            raise ValueError("Unsupported OpenRouter prompt part")
    api_key = api_key if api_key is not None else settings.openrouter_api_key
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model if model is not None else settings.openrouter_model,
                "stream": False,
                "max_tokens": 8192,
                "messages": [{"role": "user", "content": parts}],
                "response_format": {"type": "json_object"},
            },
        )
    if response.is_error:
        # Do not log upstream bodies, CV content, or credentials.
        raise OpenRouterError(response.status_code)
    payload = response.json()
    usage = payload.get("usage") or {}
    attempt.input_tokens = usage.get("prompt_tokens")
    attempt.output_tokens = usage.get("completion_tokens")
    if payload.get("error"):
        raise OpenRouterError(int(payload["error"].get("code", 502)))
    choice = payload["choices"][0]
    if choice.get("finish_reason") in ("length", "error", "content_filter"):
        raise RuntimeError("OpenRouter did not complete the response")
    result = json.loads(choice["message"]["content"])
    if not isinstance(result, dict):
        raise RuntimeError("OpenRouter response must be a JSON object")
    return result
