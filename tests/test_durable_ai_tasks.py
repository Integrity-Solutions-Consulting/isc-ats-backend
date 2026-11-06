"""Exercise real PostgreSQL transactions; Gemini and Redis are replaced at the boundary."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.modules.ai.application import cv_parse_service, task_service
from app.modules.ai.infrastructure.models import AiTask
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.api import candidates_routes
from app.modules.recruitment.api.candidates_schemas import CandidateUpdate
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.storage.infrastructure.models import File


@pytest.fixture
async def ai_db(monkeypatch):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        factory = async_sessionmaker(
            conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        monkeypatch.setattr(task_service, "async_session_factory", factory)
        monkeypatch.setattr(cv_parse_service, "async_session_factory", factory)
        async with factory() as session:
            file = File(original_name="cv.pdf", stored_key=str(uuid4()), bucket="test")
            portal = await session.scalar(
                select(Parameter).where(
                    Parameter.type == "user_portal",
                    Parameter.code == "staff",
                )
            )
            user = User(email=f"{uuid4().hex}@test.local", portal_id=portal.id)
            session.add_all([file, user])
            await session.flush()
            candidate = Candidate(
                user_id=user.id,
                first_name="Test",
                last_name="CV",
                cv_file_id=file.id,
            )
            session.add(candidate)
            await session.commit()
        yield factory, candidate, file
        await transaction.rollback()
    await engine.dispose()


async def load_task(factory):
    async with factory() as session:
        return await session.scalar(select(AiTask).order_by(AiTask.id.desc()).limit(1))


async def make_due(factory):
    async with factory() as session:
        task = await session.scalar(select(AiTask).order_by(AiTask.id.desc()).limit(1))
        task.next_attempt_at = task_service.utcnow() - timedelta(seconds=1)
        await session.commit()


async def add_task(ai_db):
    factory, candidate, file = ai_db
    async with factory() as session:
        await task_service.schedule(session, "parse_candidate_cv", candidate.id, file.id)
        await session.commit()


async def test_task_rolls_back_with_profile_change(ai_db):
    factory, candidate, file = ai_db
    async with factory() as session:
        stored = await session.get(Candidate, candidate.id)
        stored.first_name = "Changed"
        await task_service.schedule(session, "parse_candidate_cv", candidate.id, file.id)
        await session.rollback()
    assert await load_task(factory) is None
    async with factory() as session:
        assert (await session.get(Candidate, candidate.id)).first_name == "Test"


async def test_schedule_is_idempotent_and_does_not_reset_running_work(ai_db):
    factory, candidate, file = ai_db
    await add_task(ai_db)
    queue = SimpleNamespace(enqueue=AsyncMock())
    await task_service.dispatch_once(queue)
    task = await load_task(factory)
    async with factory() as session:
        await task_service.schedule(
            session, "parse_candidate_cv", candidate.id, file.id, retry=True
        )
        await session.commit()
    await task_service.dispatch_once(queue)
    assert queue.enqueue.await_count == 1
    assert (await load_task(factory)).lease_token == task.lease_token


async def test_queue_outage_keeps_cv_and_task_for_recovery(ai_db):
    factory, candidate, file = ai_db
    await add_task(ai_db)
    queue = SimpleNamespace(enqueue=AsyncMock(side_effect=ConnectionError("Redis down")))
    await task_service.dispatch_once(queue)
    task = await load_task(factory)
    assert task.status == "pending" and task.attempts == 0
    async with factory() as session:
        assert (await session.get(Candidate, candidate.id)).cv_file_id == file.id
    queue.enqueue.side_effect = None
    await make_due(factory)
    await task_service.dispatch_once(queue)
    assert (await load_task(factory)).status == "queued"


async def test_transient_failure_has_backoff_and_stops_after_four_attempts(ai_db, monkeypatch):
    factory, candidate, file = ai_db
    await add_task(ai_db)
    parse = AsyncMock(side_effect=RuntimeError("Gemini temporarily unavailable"))
    monkeypatch.setattr(cv_parse_service, "parse_candidate_cv", parse)
    queue = SimpleNamespace(enqueue=AsyncMock())
    for attempt in range(1, 5):
        await make_due(factory)
        await task_service.dispatch_once(queue)
        task = await load_task(factory)
        await task_service.run_ai_task(task.id, task.lease_token)
        task = await load_task(factory)
        assert task.attempts == attempt
        assert task.status == ("failed" if attempt == 4 else "pending")
        if attempt < 4:
            assert task.next_attempt_at > task_service.utcnow() + timedelta(seconds=50)
    async with factory() as session:
        await task_service.schedule(
            session, "parse_candidate_cv", candidate.id, file.id, retry=True
        )
        await session.commit()
    task = await load_task(factory)
    assert task.status == "pending" and task.attempts == 0


async def test_success_and_duplicate_delivery_only_call_gemini_once(ai_db, monkeypatch):
    factory, _, _ = ai_db
    await add_task(ai_db)
    parse = AsyncMock(return_value={"skills": ["Python"]})
    monkeypatch.setattr(cv_parse_service, "parse_candidate_cv", parse)
    await task_service.dispatch_once(SimpleNamespace(enqueue=AsyncMock()))
    task = await load_task(factory)
    await task_service.run_ai_task(task.id, task.lease_token)
    await task_service.run_ai_task(task.id, task.lease_token)
    assert parse.await_count == 1
    assert (await load_task(factory)).status == "completed"


async def test_result_and_completion_are_committed_together(ai_db, monkeypatch):
    factory, candidate, _ = ai_db
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(cv_parse_service, "_download_cv", lambda *args: b"pdf")
    monkeypatch.setattr(
        cv_parse_service, "_parse_comprehensively", lambda *args: {"skills": ["Python"]}
    )
    await add_task(ai_db)
    await task_service.dispatch_once(SimpleNamespace(enqueue=AsyncMock()))
    task = await load_task(factory)
    await task_service.run_ai_task(task.id, task.lease_token)
    async with factory() as session:
        stored_task = await session.get(AiTask, task.id)
        stored_candidate = await session.get(Candidate, candidate.id)
        assert stored_task.status == "completed" and stored_task.lease_token is None
        assert stored_candidate.parsed_data == {"skills": ["Python"]}
        assert stored_candidate.last_parsed_at is not None


async def test_expired_worker_cannot_commit_a_result_for_same_cv(ai_db):
    factory, candidate, _ = ai_db
    await add_task(ai_db)
    await task_service.dispatch_once(SimpleNamespace(enqueue=AsyncMock()))
    task = await load_task(factory)
    with pytest.raises(task_service.SupersededCvError):
        async with factory() as session:
            stored = await session.get(Candidate, candidate.id)
            stored.parsed_data = {"skills": ["Expired worker"]}
            await task_service.complete_in_transaction(session, task.id, "expired-token")
            await session.commit()
    async with factory() as session:
        assert (await session.get(Candidate, candidate.id)).parsed_data is None


async def test_analysis_status_does_not_expose_another_candidates_task(ai_db):
    from fastapi import HTTPException

    from app.core.dependencies import CurrentUser

    factory, candidate, _ = ai_db
    await add_task(ai_db)
    async with factory() as session:
        with pytest.raises(HTTPException) as denied:
            await candidates_routes.cv_analysis_status(
                candidate.id,
                candidates_routes.get_service(session),
                session,
                CurrentUser(user_id=candidate.user_id + 1000, ip=None, portal="candidate"),
            )
        assert denied.value.status_code == 403


async def test_expired_delivery_gets_new_lease_and_rejects_old_worker(ai_db, monkeypatch):
    factory, _, _ = ai_db
    await add_task(ai_db)
    parse = AsyncMock()
    monkeypatch.setattr(cv_parse_service, "parse_candidate_cv", parse)
    queue = SimpleNamespace(enqueue=AsyncMock())
    await task_service.dispatch_once(queue)
    old = await load_task(factory)
    await make_due(factory)
    await task_service.dispatch_once(queue)
    new = await load_task(factory)
    assert new.lease_token != old.lease_token
    await task_service.run_ai_task(old.id, old.lease_token)
    parse.assert_not_awaited()
    await task_service.run_ai_task(new.id, new.lease_token)
    assert (await load_task(factory)).status == "completed"


async def test_cv_replacement_while_gemini_runs_discards_old_result(ai_db, monkeypatch):
    factory, candidate, file = ai_db
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    async def replace_cv(fn, *args):
        if fn is cv_parse_service._download_cv:
            return b"fake pdf"
        async with factory() as session:
            stored = await session.get(Candidate, candidate.id)
            stored.cv_file_id = None
            await session.commit()
        return {"skills": ["Old CV"]}

    monkeypatch.setattr("asyncio.to_thread", replace_cv)
    with pytest.raises(task_service.SupersededCvError):
        await cv_parse_service.parse_candidate_cv(
            candidate.id,
            strict=True,
            expected_file_id=file.id,
        )
    async with factory() as session:
        stored = await session.get(Candidate, candidate.id)
        assert stored.cv_file_id is None and stored.parsed_data is None


async def test_linking_cv_does_not_call_queue_even_when_it_is_down(ai_db):
    factory, candidate, file = ai_db
    from app.core.dependencies import CurrentUser

    async with factory() as session:
        stored = await session.get(Candidate, candidate.id)
        stored.cv_file_id = None
        await session.commit()
        service = candidates_routes.get_service(session)
        queue = SimpleNamespace(enqueue=AsyncMock(side_effect=ConnectionError("Redis down")))
        result = await candidates_routes.update_candidate(
            candidate.id,
            CandidateUpdate(cv_file_id=file.id),
            service,
            session,
            CurrentUser(user_id=candidate.user_id, ip="127.0.0.1"),
            queue,
        )
        await session.commit()
        assert result.cv_file_id == file.id
        queue.enqueue.assert_not_awaited()
    assert (await load_task(factory)).status == "pending"


@pytest.mark.parametrize("code,status", [(403, "failed"), (429, "pending"), (503, "pending")])
def test_failure_classification_preserves_wrapped_provider_errors(code, status):
    provider_error = RuntimeError("provider")
    provider_error.code = code
    wrapped = RuntimeError("All models failed")
    wrapped.__cause__ = provider_error
    assert task_service.failure_policy(wrapped, 1)[0] == status
