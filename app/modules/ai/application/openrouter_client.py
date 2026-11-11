"""OpenRouter backup for the existing text and CV page-image prompts."""

import base64
import json
from typing import Any

import httpx

from app.core.config import settings


class OpenRouterError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"OpenRouter request failed (HTTP {status_code})")


def call_openrouter(contents: list[Any]) -> dict:
    if not settings.openrouter_api_key:
        raise ValueError("OpenRouter is not configured")
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
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={
                "model": settings.openrouter_model,
                "max_tokens": 8192,
                "messages": [{"role": "user", "content": parts}],
                "response_format": {"type": "json_object"},
            },
        )
    if response.is_error:
        # Do not log upstream bodies, CV content, or credentials.
        raise OpenRouterError(response.status_code)
    payload = response.json()
    if payload.get("error"):
        raise OpenRouterError(int(payload["error"].get("code", 502)))
    choice = payload["choices"][0]
    if choice.get("finish_reason") in ("length", "error", "content_filter"):
        raise RuntimeError("OpenRouter did not complete the response")
    result = json.loads(choice["message"]["content"])
    if not isinstance(result, dict):
        raise RuntimeError("OpenRouter response must be a JSON object")
    return result
