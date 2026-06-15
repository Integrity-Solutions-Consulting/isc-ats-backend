"""Tests for the email subsystem: transport selection, templating, and dispatch
logging. No real SMTP/HTTP is performed — a fake sender stands in for transport.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.comms.application.email_dispatch_service import EmailDispatchService
from app.modules.comms.application.email_sender import EmailMessage, EmailResult
from app.modules.comms.application.email_templates import (
    render_interview_invitation_email,
    render_stage_change_email,
    render_verification_email,
)
from app.modules.comms.infrastructure.email_sender_factory import build_email_sender
from app.modules.comms.infrastructure.models import EmailLog
from app.modules.comms.infrastructure.resend_email_sender import ResendEmailSender
from app.modules.comms.infrastructure.smtp_email_sender import SmtpEmailSender
from app.modules.org.infrastructure.models import Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository


class _FakeSender:
    """In-memory EmailSender that records messages and returns a canned result."""

    def __init__(self, *, success: bool) -> None:
        self._success = success
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> EmailResult:
        self.sent.append(message)
        if self._success:
            return EmailResult(success=True, provider_message_id="fake-123")
        return EmailResult(success=False, error_detail="boom")


async def _ensure_email_status(session: AsyncSession) -> None:
    """Make email_status seed available within the test transaction even if the
    seed migration has not been applied yet."""
    repo = ParameterRepository(session)
    for code, name in (("sent", "Enviado"), ("failed", "Error")):
        if await repo.get_by_type_and_code("email_status", code) is None:
            session.add(Parameter(type="email_status", code=code, name=name))
    await session.flush()


# ── Transport selection ─────────────────────────────────────────────────────


def test_build_email_sender_defaults_to_smtp() -> None:
    sender = build_email_sender(Settings(_env_file=None, email_provider="smtp"))
    assert isinstance(sender, SmtpEmailSender)


def test_build_email_sender_selects_resend() -> None:
    sender = build_email_sender(Settings(_env_file=None, email_provider="resend"))
    assert isinstance(sender, ResendEmailSender)


# ── Templating ──────────────────────────────────────────────────────────────


def test_render_verification_email_embeds_url() -> None:
    url = "http://localhost:3000/api/auth/verify?token=abc.def.ghi"
    rendered = render_verification_email(url)
    assert rendered.subject
    assert url in rendered.html_body
    assert url in rendered.text_body


def test_render_stage_change_email_includes_details() -> None:
    rendered = render_stage_change_email("Ana", "Desarrollador Backend", "Entrevista técnica")
    assert "Desarrollador Backend" in rendered.subject
    for body in (rendered.html_body, rendered.text_body):
        assert "Ana" in body
        assert "Desarrollador Backend" in body
        assert "Entrevista técnica" in body


def test_render_interview_invitation_includes_link_and_local_time() -> None:
    from datetime import UTC, datetime

    # 14:30 UTC -> 09:30 Ecuador (UTC-5)
    scheduled = datetime(2026, 6, 16, 14, 30, tzinfo=UTC)
    rendered = render_interview_invitation_email(
        "Ana", "Backend Dev", scheduled, "https://teams.microsoft.com/l/meetup-join/abc"
    )
    assert "Backend Dev" in rendered.subject
    for body in (rendered.html_body, rendered.text_body):
        assert "https://teams.microsoft.com/l/meetup-join/abc" in body
        assert "16/06/2026 09:30" in body  # converted to Ecuador time


# ── Dispatch logging ────────────────────────────────────────────────────────


async def test_dispatch_logs_sent(session: AsyncSession) -> None:
    await _ensure_email_status(session)
    to_email = f"{uuid.uuid4().hex[:10]}@test.local"

    ok = await EmailDispatchService(session, _FakeSender(success=True)).send(
        EmailMessage(to_email=to_email, subject="Hola", html_body="<p>x</p>")
    )

    assert ok is True
    log = (
        await session.execute(select(EmailLog).where(EmailLog.to_email == to_email))
    ).scalar_one()
    sent = await ParameterRepository(session).get_by_type_and_code("email_status", "sent")
    assert log.status_id == sent.id
    assert log.provider_message_id == "fake-123"
    assert log.error_detail is None


async def test_dispatch_logs_failed(session: AsyncSession) -> None:
    await _ensure_email_status(session)
    to_email = f"{uuid.uuid4().hex[:10]}@test.local"

    ok = await EmailDispatchService(session, _FakeSender(success=False)).send(
        EmailMessage(to_email=to_email, subject="Hola", html_body="<p>x</p>")
    )

    assert ok is False
    log = (
        await session.execute(select(EmailLog).where(EmailLog.to_email == to_email))
    ).scalar_one()
    failed = await ParameterRepository(session).get_by_type_and_code(
        "email_status", "failed"
    )
    assert log.status_id == failed.id
    assert log.error_detail == "boom"
