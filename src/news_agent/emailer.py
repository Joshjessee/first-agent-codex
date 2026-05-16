from __future__ import annotations

from email.message import EmailMessage
import os
import smtplib

from news_agent.config import EmailConfig
from news_agent.digest import Digest


def send_digest_email(digest: Digest, email_config: EmailConfig) -> None:
    smtp_host = _required_env("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT") or "465")
    smtp_username = _required_env("SMTP_USERNAME")
    smtp_password = _required_env("SMTP_PASSWORD")
    email_from = os.getenv("EMAIL_FROM") or smtp_username
    recipients = _resolve_recipients(email_config.recipients)

    message = EmailMessage()
    message["Subject"] = digest.subject(email_config.subject_prefix)
    message["From"] = email_from
    message["To"] = ", ".join(recipients)
    message.set_content(digest.to_text())
    message.add_alternative(digest.to_html(), subtype="html")

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as smtp:
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


def _resolve_recipients(config_recipients: tuple[str, ...]) -> tuple[str, ...]:
    if config_recipients:
        return config_recipients

    email_to = os.getenv("EMAIL_TO", "")
    recipients = tuple(
        recipient.strip()
        for recipient in email_to.replace(";", ",").split(",")
        if recipient.strip()
    )
    if recipients:
        return recipients

    raise RuntimeError("Config email.recipients or EMAIL_TO must define at least one recipient.")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
