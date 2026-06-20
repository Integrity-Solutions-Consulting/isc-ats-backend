"""R2: analyze_application is idempotent — a re-trigger must not re-spend Gemini.

The "safety net" that re-runs the analysis when a recruiter opens the profile
must NOT call Gemini again if the application already has a match_score. Only an
explicit force=True (manual re-analysis) bypasses the guard.

These are focused unit tests: the idempotency decision lives in Phase 1 right
after the application is loaded, before any candidate/CV/MinIO/Gemini work. We
monkeypatch the DB collaborators so the test needs no Postgres and no Gemini.
The guard's effect is observed via `session.get`: if it is called, the function
moved PAST the guard; if not, the guard short-circuited.
"""

from __future__ import annotations

from decimal import Decimal

from app.modules.ai.application import analysis_service


class _FakeApplication:
    def __init__(self, match_score: Decimal | None) -> None:
        self.id = 1
        self.candidate_id = 10
        self.vacancy_id = 20
        self.match_score = match_score


class _FakeRepo:
    """Stands in for ApplicationRepository — only .get() is exercised here."""

    def __init__(self, application: _FakeApplication) -> None:
        self._application = application

    def __call__(self, _session: object) -> _FakeRepo:
        # ApplicationRepository(session) is constructed inside analyze_application;
        # the monkeypatched class is this instance, so calling it returns self.
        return self

    async def get(self, _application_id: int) -> _FakeApplication:
        return self._application


class _FakeSession:
    """Records whether the function reached the post-guard DB reads."""

    def __init__(self) -> None:
        self.get_called = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get(self, _model: object, _pk: object) -> None:
        self.get_called = True
        return None  # candidate not found -> function returns early past the guard


def _patch(monkeypatch, application: _FakeApplication) -> _FakeSession:
    session = _FakeSession()
    repo = _FakeRepo(application)
    monkeypatch.setattr(analysis_service, "async_session_factory", lambda: session)
    monkeypatch.setattr(analysis_service, "ApplicationRepository", repo)
    monkeypatch.setattr(
        analysis_service.settings, "gemini_api_key", "test-key", raising=False
    )
    return session


async def test_skips_when_already_scored(monkeypatch) -> None:
    session = _patch(monkeypatch, _FakeApplication(match_score=Decimal("85")))

    await analysis_service.analyze_application(1)

    assert session.get_called is False  # guard short-circuited, never reached Gemini path


async def test_force_bypasses_guard_when_already_scored(monkeypatch) -> None:
    session = _patch(monkeypatch, _FakeApplication(match_score=Decimal("85")))

    await analysis_service.analyze_application(1, force=True)

    assert session.get_called is True  # force=True moved past the guard


async def test_proceeds_when_not_yet_scored(monkeypatch) -> None:
    session = _patch(monkeypatch, _FakeApplication(match_score=None))

    await analysis_service.analyze_application(1)

    assert session.get_called is True  # never analyzed -> proceeds normally
