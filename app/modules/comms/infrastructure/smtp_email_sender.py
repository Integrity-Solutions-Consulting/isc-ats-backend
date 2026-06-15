import asyncio
import smtplib
import uuid
from email.message import EmailMessage as MimeMessage

from app.modules.comms.application.email_sender import EmailMessage, EmailResult


class SmtpEmailSender:
    """EmailSender backed by an SMTP relay (Gmail by default).

    The stdlib smtplib client is blocking, so the actual send runs in a worker
    thread (asyncio.to_thread) to keep the event loop free. Fine for low/moderate
    volume; swap for ResendEmailSender once the sending domain is DNS-verified.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender_name: str,
        sender_email: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender_name = sender_name
        # Fall back to the SMTP username (the mailbox) when no explicit From is set.
        self._sender_email = sender_email or username

    async def send(self, message: EmailMessage) -> EmailResult:
        return await asyncio.to_thread(self._send_blocking, message)

    def _build_mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = f"{self._sender_name} <{self._sender_email}>"
        mime["To"] = message.to_email
        mime["Subject"] = message.subject
        # A plain-text part keeps the message readable in text-only clients and
        # improves spam scoring; the HTML alternative is the rich rendering.
        mime.set_content(
            message.text_body or "Abre este correo en un cliente compatible con HTML."
        )
        mime.add_alternative(message.html_body, subtype="html")
        return mime

    def _send_blocking(self, message: EmailMessage) -> EmailResult:
        try:
            mime = self._build_mime(message)
            with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(self._username, self._password)
                smtp.send_message(mime)
            # SMTP returns no provider id; synthesize one for traceability in logs.
            return EmailResult(
                success=True, provider_message_id=f"smtp-{uuid.uuid4().hex}"
            )
        except Exception as exc:  # noqa: BLE001 - any SMTP/connection error becomes a logged failure
            return EmailResult(success=False, error_detail=str(exc)[:1000])
