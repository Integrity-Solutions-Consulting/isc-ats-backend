from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class MeetingRequest:
    """An online meeting to be created. Times are timezone-aware.

    `organizer_email` is the M365 UPN of the host (the interviewer); external
    attendees join anonymously via the link, so they need no M365 account.
    """

    subject: str
    start: datetime
    end: datetime
    organizer_email: str
    attendee_emails: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MeetingResult:
    """Outcome of a meeting-creation attempt. Never an exception — failures carry
    `success=False` so the caller can skip storing a link."""

    success: bool
    join_url: str | None = None
    meeting_id: str | None = None
    error_detail: str | None = None


class MeetingProvider(Protocol):
    """Port: create an online meeting through whichever backend is configured.

    Implementations MUST NOT raise; they return MeetingResult(success=False, ...)
    so a meeting failure never breaks interview scheduling.
    """

    async def create_meeting(self, request: MeetingRequest) -> MeetingResult: ...
