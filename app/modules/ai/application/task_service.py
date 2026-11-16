"""Durable AI outbox. Requests only write SQL; the dispatcher talks to Redis.

Pending work and delayed retries survive restarts and queue outages. A lease
recovers jobs lost in Redis or interrupted in a worker. CV writes additionally
check the file revision so an old result cannot overwrite a replacement CV.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.task_queue import TaskQueue, register_task
from app.modules.ai.infrastructure.models import AiTask
from app.modules.ai.infrastructure.usage_tracking import persist_usage
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 4
RETRY_SECONDS = (60, 300, 900)
LEASE_SECONDS = 600


def utcnow() -> datetime:
    return datetime.now(UTC)


async def schedule(
    session: AsyncSession,
    action: str,
    entity_id: int,
    file_id: int,
    *,
    retry: bool = False,
) -> None:
    now = utcnow()
    stmt = insert(AiTask).values(
        action=action,
        entity_id=entity_id,
        file_id=file_id,
        status="pending",
        attempts=0,
        next_attempt_at=now,
        updated_at=now,
    )
    if retry:
        stmt = stmt.on_conflict_do_update(
            index_elements=[AiTask.action, AiTask.entity_id, AiTask.file_id],
            set_={
                "status": "pending",
                "attempts": 0,
                "error_code": None,
                "lease_token": None,
                "next_attempt_at": now,
                "updated_at": now,
            },
            where=AiTask.status.in_(["failed", "completed", "superseded"]),
        )
    else:
        stmt = stmt.on_conflict_do_nothing()
    await session.execute(stmt)


async def task_status(
    session: AsyncSession,
    action: str,
    entity_id: int,
    file_id: int | None,
    *,
    completed: bool = False,
) -> dict:
    task = (
        await session.scalar(
            select(AiTask).where(
                AiTask.action == action,
                AiTask.entity_id == entity_id,
                AiTask.file_id == file_id,
            )
        )
        if file_id
        else None
    )
    return {
        "status": task.status if task else ("completed" if completed else "unavailable"),
        "attempts": task.attempts if task else 0,
        "next_attempt_at": task.next_attempt_at if task and task.status == "pending" else None,
    }


def failure_policy(exc: Exception, attempts: int) -> tuple[str, str]:
    cause: BaseException | None = exc
    while cause:
        code = getattr(cause, "code", None) or getattr(cause, "status_code", None)
        if code in (400, 401, 403, 404):
            return "failed", "configuration"
        cause = cause.__cause__
    if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
        return "failed", "invalid_input"
    return ("failed" if attempts >= MAX_ATTEMPTS else "pending"), "temporary"


async def run_ai_task(task_id: int, token: str) -> None:
    async with async_session_factory() as session:
        task = await session.scalar(select(AiTask).where(AiTask.id == task_id).with_for_update())
        if not task or task.lease_token != token or task.status != "queued":
            return
        task.status = "processing"
        task.attempts += 1
        task.updated_at = utcnow()
        task.next_attempt_at = utcnow() + timedelta(seconds=LEASE_SECONDS)
        action, entity_id, file_id, attempts = (
            task.action,
            task.entity_id,
            task.file_id,
            task.attempts,
        )
        await session.commit()

    status, error_code = "completed", None
    failure_code = None
    logger.info("Tarea de IA iniciada", extra={
        "task_id": task_id, "action": action, "entity_id": entity_id, "attempt": attempts,
    })
    try:
        async with asyncio.timeout(240):
            if action == "parse_candidate_cv":
                from app.modules.ai.application.cv_parse_service import parse_candidate_cv

                await parse_candidate_cv(
                    entity_id,
                    strict=True,
                    expected_file_id=file_id,
                    task_id=task_id,
                    lease_token=token,
                )
            else:
                from app.modules.ai.application.analysis_service import analyze_application

                await analyze_application(
                    entity_id,
                    force=True,
                    strict=True,
                    expected_file_id=file_id,
                    task_id=task_id,
                    lease_token=token,
                )
    except SupersededCvError:
        status = "superseded"
    except Exception as exc:
        logger.exception("AI task %d attempt %d failed", task_id, attempts)
        status, error_code = failure_policy(exc, attempts)
        cause: BaseException | None = exc
        while cause:
            code = getattr(cause, "code", None) or getattr(cause, "status_code", None)
            if code:
                failure_code = str(code)[:40]
                break
            cause = cause.__cause__
        failure_code = failure_code or type(exc).__name__[:40]

    async with async_session_factory() as session:
        task = await session.scalar(select(AiTask).where(AiTask.id == task_id).with_for_update())
        if not task or task.lease_token != token:
            logger.info("Tarea de IA ya finalizada o reemplazada", extra={
                "task_id": task_id, "action": action,
                "status": task.status if task else "missing",
            })
            return
        task.status, task.error_code = status, error_code
        task.lease_token = None
        task.updated_at = utcnow()
        if status == "pending":
            task.next_attempt_at = utcnow() + timedelta(seconds=RETRY_SECONDS[attempts - 1])
        await session.commit()

    logger.info("Tarea de IA finalizada", extra={
        "task_id": task_id, "action": action, "status": status,
        "attempt": attempts, "error_code": failure_code or error_code,
    })
    if failure_code:
        try:
            await persist_usage({
                "action": f"{action}:task", "status": "error", "error_code": failure_code,
                "task_id": task_id, "entity_id": entity_id,
            })
        except Exception:
            logger.exception("No se pudo guardar el fallo de tarea %s", task_id)


class SupersededCvError(Exception):
    """The candidate replaced or removed the CV while work was running."""


async def complete_in_transaction(
    session: AsyncSession,
    task_id: int | None,
    token: str | None,
) -> None:
    """Commit the result and its acknowledgement together, fencing expired workers."""
    if task_id is None:
        return
    task = await session.scalar(select(AiTask).where(AiTask.id == task_id).with_for_update())
    if not task or task.lease_token != token or task.status != "processing":
        raise SupersededCvError()
    task.status, task.error_code, task.lease_token = "completed", None, None
    task.updated_at = utcnow()


async def current_candidate(
    session: AsyncSession,
    candidate_id: int,
    file_id: int | None,
    *,
    lock: bool = False,
) -> Candidate:
    stmt = select(Candidate).where(Candidate.id == candidate_id)
    if lock:
        stmt = stmt.with_for_update()
    candidate = await session.scalar(stmt)
    if not candidate or not candidate.is_active or candidate.cv_file_id != file_id:
        raise SupersededCvError()
    return candidate


async def dispatch_once(queue: TaskQueue) -> None:
    now = utcnow()
    deliveries = []
    async with async_session_factory() as session:
        tasks = (
            await session.scalars(
                select(AiTask)
                .where(
                    AiTask.status.in_(["pending", "queued", "processing"]),
                    AiTask.next_attempt_at <= now,
                )
                .order_by(AiTask.next_attempt_at)
                .limit(20)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for task in tasks:
            if task.attempts >= MAX_ATTEMPTS:
                task.status, task.error_code = "failed", "interrupted"
                continue
            task.status, task.lease_token = "queued", str(uuid4())
            task.updated_at = now
            task.next_attempt_at = now + timedelta(seconds=LEASE_SECONDS)
            deliveries.append((task.id, task.lease_token))
        await session.commit()
    for task_id, token in deliveries:
        try:
            await queue.enqueue("run_ai_task", task_id, token)
            logger.info("Tarea de IA encolada", extra={"task_id": task_id})
        except Exception:
            logger.exception("Queue unavailable; AI task %d remains saved", task_id)
            async with async_session_factory() as session:
                task = await session.scalar(
                    select(AiTask).where(AiTask.id == task_id).with_for_update(),
                )
                if task and task.lease_token == token and task.status == "queued":
                    task.status = "pending"
                    task.lease_token = None
                    task.next_attempt_at = utcnow() + timedelta(seconds=30)
                    await session.commit()


async def dispatch_loop(queue: TaskQueue) -> None:
    while True:
        try:
            await dispatch_once(queue)
        except Exception:
            logger.exception("AI dispatcher will retry after database/queue failure")
        await asyncio.sleep(15)


async def schedule_application(
    session: AsyncSession,
    application: Application,
    *,
    retry: bool = False,
) -> None:
    candidate = await session.get(Candidate, application.candidate_id)
    if candidate and candidate.cv_file_id:
        await schedule(
            session, "analyze_application", application.id, candidate.cv_file_id, retry=retry
        )


register_task("run_ai_task", run_ai_task)
