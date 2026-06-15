from app.core.config import Settings
from app.core.config import settings as global_settings
from app.modules.comms.application.email_sender import EmailSender
from app.modules.comms.infrastructure.resend_email_sender import ResendEmailSender
from app.modules.comms.infrastructure.smtp_email_sender import SmtpEmailSender


def build_email_sender(config: Settings | None = None) -> EmailSender:
    """Select the email transport from settings.email_provider.

    "smtp"   -> Gmail/SMTP (active default)
    "resend" -> Resend HTTP API (future; needs a verified domain + api key)
    """
    config = config or global_settings
    if config.email_provider == "resend":
        return ResendEmailSender(
            api_key=config.resend_api_key,
            sender_name=config.email_sender_name,
            sender_email=config.email_sender_email,
        )
    return SmtpEmailSender(
        host=config.smtp_host,
        port=config.smtp_port,
        username=config.smtp_username,
        password=config.smtp_password,
        sender_name=config.email_sender_name,
        sender_email=config.email_sender_email,
    )
