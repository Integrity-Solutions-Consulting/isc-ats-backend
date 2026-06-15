from app.core.config import Settings
from app.core.config import settings as global_settings
from app.modules.comms.application.meeting_provider import MeetingProvider
from app.modules.comms.infrastructure.disabled_meeting_provider import (
    DisabledMeetingProvider,
)
from app.modules.comms.infrastructure.graph_meeting_provider import GraphMeetingProvider


def build_meeting_provider(config: Settings | None = None) -> MeetingProvider:
    """Select the meeting backend from settings.

    "graph" + complete Azure credentials -> GraphMeetingProvider (Teams).
    Anything else -> DisabledMeetingProvider (scheduling works, no link created).
    """
    config = config or global_settings
    if (
        config.meetings_provider == "graph"
        and config.azure_tenant_id
        and config.azure_client_id
        and config.azure_client_secret
    ):
        return GraphMeetingProvider(
            config.azure_tenant_id,
            config.azure_client_id,
            config.azure_client_secret,
        )
    return DisabledMeetingProvider()
