"""Persist provider attempts without storing CVs, prompts, or credentials."""

import asyncio
import contextvars
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from app.core.database import async_session_factory
from app.modules.ai.infrastructure.models import AiUsageLog

logger = logging.getLogger(__name__)
_observer: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = (
    contextvars.ContextVar("ai_usage_observer", default=None)
)


@dataclass
class Attempt:
    input_tokens: int | None = None
    output_tokens: int | None = None


@contextmanager
def track_attempt(model: str) -> Iterator[Attempt]:
    start = time.monotonic()
    attempt = Attempt()
    code = None
    try:
        yield attempt
    except Exception as exc:
        raw = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        code = str(raw)[:40] if raw else type(exc).__name__[:40]
        raise
    finally:
        values = {
            "model": model, "status": "error" if code else "success", "error_code": code,
            "duration_ms": round((time.monotonic() - start) * 1000),
            "input_tokens": attempt.input_tokens, "output_tokens": attempt.output_tokens,
        }
        logger.info("AI provider attempt model=%s status=%s error_code=%s duration_ms=%s",
                    model, values["status"], code, values["duration_ms"])
        observer = _observer.get()
        if observer is not None:
            observer(values)


async def persist_usage(values: dict[str, Any]) -> None:
    async with async_session_factory() as session:
        session.add(AiUsageLog(**values))
        await session.commit()


async def tracked_to_thread(
    fn: Callable[..., Any], *args: Any, action: str,
    task_id: int | None = None, entity_id: int | None = None,
) -> Any:
    loop = asyncio.get_running_loop()

    def observe(values: dict[str, Any]) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(
                persist_usage({
                    **values, "action": action, "task_id": task_id, "entity_id": entity_id,
                }),
                loop,
            )
            future.result(timeout=5)
        except Exception:
            logger.exception("Could not save AI usage; action=%s task_id=%s", action, task_id)

    token = _observer.set(observe)
    try:
        return await asyncio.to_thread(fn, *args)
    finally:
        _observer.reset(token)
