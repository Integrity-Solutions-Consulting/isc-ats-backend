from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmailMessage:
    """A renderable outbound email. Bodies are already localized and templated."""

    to_email: str
    subject: str
    html_body: str
    text_body: str | None = None


@dataclass(frozen=True)
class EmailResult:
    """Outcome of a single send attempt — maps 1:1 to a comms.email_logs row."""

    success: bool
    provider_message_id: str | None = None
    error_detail: str | None = None


class EmailSender(Protocol):
    """Port: send one email through whichever transport is configured.

    Implementations MUST NOT raise on a delivery failure. They return
    EmailResult(success=False, error_detail=...) so the caller can record the
    attempt without aborting the surrounding operation (e.g. registration).
    """

    async def send(self, message: EmailMessage) -> EmailResult: ...
