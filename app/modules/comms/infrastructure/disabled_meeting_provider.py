from app.modules.comms.application.meeting_provider import MeetingRequest, MeetingResult


class DisabledMeetingProvider:
    """No-op MeetingProvider used when no meeting backend is configured.

    Always returns a non-success result so callers skip storing a meeting link.
    This keeps interview scheduling fully functional without Teams configured.
    """

    async def create_meeting(self, request: MeetingRequest) -> MeetingResult:
        return MeetingResult(success=False, error_detail="Meetings provider is disabled")
