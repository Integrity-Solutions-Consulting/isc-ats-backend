import httpx

from app.modules.comms.application.email_sender import EmailMessage, EmailResult

_RESEND_ENDPOINT = "https://api.resend.com/emails"


class ResendEmailSender:
    """EmailSender backed by the Resend HTTP API (future transport).

    Activate by setting EMAIL_PROVIDER=resend once the sending domain is verified
    in Resend (SPF/DKIM DNS records) and RESEND_API_KEY is configured. Until then
    SmtpEmailSender is the active transport.
    """

    def __init__(self, api_key: str, sender_name: str, sender_email: str) -> None:
        self._api_key = api_key
        self._from = f"{sender_name} <{sender_email}>"

    async def send(self, message: EmailMessage) -> EmailResult:
        payload: dict[str, object] = {
            "from": self._from,
            "to": [message.to_email],
            "subject": message.subject,
            "html": message.html_body,
        }
        if message.text_body:
            payload["text"] = message.text_body
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    _RESEND_ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
            if resp.status_code >= 400:
                return EmailResult(
                    success=False,
                    error_detail=f"Resend {resp.status_code}: {resp.text[:500]}",
                )
            provider_id = resp.json().get("id")
            return EmailResult(success=True, provider_message_id=provider_id)
        except Exception as exc:  # noqa: BLE001 - any HTTP/connection error becomes a logged failure
            return EmailResult(success=False, error_detail=str(exc)[:1000])
