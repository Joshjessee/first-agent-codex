from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate
from email.utils import make_msgid
import os
import smtplib
import ssl

from news_agent.config import EmailConfig
from news_agent.digest import Digest


SMTP_TIMEOUT_SECONDS = 30


def send_digest_email(digest: Digest, email_config: EmailConfig) -> None:
    smtp_host = _required_env("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT") or "465")
    smtp_username = _required_env("SMTP_USERNAME")
    smtp_password = _required_env("SMTP_PASSWORD")
    email_from = os.getenv("EMAIL_FROM") or smtp_username
    recipients = _resolve_recipients(email_config.recipients)
    security = _smtp_security(smtp_port)

    message = EmailMessage()
    message["Subject"] = digest.subject(email_config.subject_prefix)
    message["From"] = email_from
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=email_from.rsplit("@", 1)[-1] if "@" in email_from else None)
    message.set_content(digest.to_text())
    message.add_alternative(digest.to_html(), subtype="html")

    # An explicit default context makes Python verify the server certificate,
    # so the SMTP password is never sent to an impostor server.
    context = ssl.create_default_context()
    if security == "ssl":
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=SMTP_TIMEOUT_SECONDS, context=context) as smtp:
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.starttls(context=context)
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


def _smtp_security(port: int) -> str:
    """Pick "ssl" (port 465) or "starttls" (port 587), overridable with SMTP_SECURITY."""
    value = (os.getenv("SMTP_SECURITY") or "").strip().lower()
    if value in {"ssl", "starttls"}:
        return value
    if value:
        raise RuntimeError("SMTP_SECURITY must be 'ssl' or 'starttls'.")
    return "starttls" if port in {25, 587} else "ssl"


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
