"""Tests for the meeting-provider selection and the disabled fallback.

The Graph transport itself is not exercised (it needs live Azure credentials);
these cover provider selection and the safe no-op default.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.modules.comms.application.meeting_provider import MeetingRequest
from app.modules.comms.infrastructure.disabled_meeting_provider import (
    DisabledMeetingProvider,
)
from app.modules.comms.infrastructure.graph_meeting_provider import GraphMeetingProvider
from app.modules.comms.infrastructure.meeting_provider_factory import (
    build_meeting_provider,
)


def _graph_settings() -> Settings:
    return Settings(
        _env_file=None,
        meetings_provider="graph",
        azure_tenant_id="t",
        azure_client_id="c",
        azure_client_secret="s",
    )


def test_factory_defaults_to_disabled() -> None:
    provider = build_meeting_provider(Settings(_env_file=None, meetings_provider="disabled"))
    assert isinstance(provider, DisabledMeetingProvider)


def test_factory_requires_complete_azure_credentials() -> None:
    # "graph" but missing secrets must fall back to the disabled provider.
    incomplete = Settings(_env_file=None, meetings_provider="graph", azure_tenant_id="t")
    assert isinstance(build_meeting_provider(incomplete), DisabledMeetingProvider)


def test_factory_selects_graph_when_configured() -> None:
    assert isinstance(build_meeting_provider(_graph_settings()), GraphMeetingProvider)


async def test_disabled_provider_returns_non_success() -> None:
    now = datetime.now(UTC)
    result = await DisabledMeetingProvider().create_meeting(
        MeetingRequest(
            subject="X",
            start=now,
            end=now + timedelta(hours=1),
            organizer_email="hr@example.com",
        )
    )
    assert result.success is False
    assert result.join_url is None
