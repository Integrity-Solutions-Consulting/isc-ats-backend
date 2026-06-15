from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.comms.application.email_sender import EmailMessage, EmailSender
from app.modules.comms.infrastructure.models import EmailLog
from app.modules.org.infrastructure.parameters_repository import ParameterRepository


class EmailDispatchService:
    """Sends an email through the configured transport and records every attempt
    in comms.email_logs.

    Never raises on a delivery failure — a failed send is logged with status
    "failed" and the caller decides what to do with the boolean result.
    """

    def __init__(self, session: AsyncSession, sender: EmailSender) -> None:
        self._session = session
        self._sender = sender
        self._parameters = ParameterRepository(session)

    async def send(self, message: EmailMessage) -> bool:
        result = await self._sender.send(message)
        status_code = "sent" if result.success else "failed"
        status = await self._parameters.get_by_type_and_code("email_status", status_code)
        # email_status is seeded by migration; guard anyway so a missing seed
        # degrades to "email sent, not logged" instead of crashing the send path.
        if status is not None:
            self._session.add(
                EmailLog(
                    to_email=message.to_email,
                    subject=message.subject,
                    status_id=status.id,
                    provider_message_id=result.provider_message_id,
                    error_detail=result.error_detail,
                    created_by=None,
                )
            )
            await self._session.flush()
        return result.success
