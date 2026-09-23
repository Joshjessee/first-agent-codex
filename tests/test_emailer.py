from datetime import datetime, timezone
import os
import ssl
from unittest import mock
import unittest

from news_agent.config import EmailConfig
from news_agent.digest import Digest
from news_agent.digest import DigestArticle
from news_agent.emailer import send_digest_email


class EmailerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.digest = Digest(
            topic="AI",
            generated_at=datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc),
            articles=[
                DigestArticle(
                    title="Example headline",
                    source="Example source",
                    url="https://example.com/article",
                    summary="A concise summary.",
                    why_it_matters="A concise reason.",
                )
            ],
        )

    @mock.patch.dict(
        os.environ,
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "secret",
            "EMAIL_FROM": "digest@example.com",
        },
        clear=True,
    )
    @mock.patch("news_agent.emailer.smtplib.SMTP_SSL")
    def test_send_digest_email_uses_config_recipients(self, smtp_ssl: mock.Mock) -> None:
        smtp = smtp_ssl.return_value.__enter__.return_value

        send_digest_email(
            self.digest,
            EmailConfig(
                subject_prefix="Daily Research Digest",
                recipients=("one@example.com", "two@example.com"),
            ),
        )

        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "one@example.com, two@example.com")
        smtp.login.assert_called_once_with("sender@example.com", "secret")

    @mock.patch.dict(
        os.environ,
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "secret",
            "EMAIL_TO": "legacy@example.com",
        },
        clear=True,
    )
    @mock.patch("news_agent.emailer.smtplib.SMTP_SSL")
    def test_send_digest_email_falls_back_to_email_to(self, smtp_ssl: mock.Mock) -> None:
        smtp = smtp_ssl.return_value.__enter__.return_value

        send_digest_email(self.digest, EmailConfig())

        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "legacy@example.com")

    @mock.patch.dict(
        os.environ,
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "secret",
            "EMAIL_FROM": "",
        },
        clear=True,
    )
    @mock.patch("news_agent.emailer.smtplib.SMTP_SSL")
    def test_send_digest_email_defaults_optional_empty_env_values(self, smtp_ssl: mock.Mock) -> None:
        smtp = smtp_ssl.return_value.__enter__.return_value

        send_digest_email(self.digest, EmailConfig(recipients=("reader@example.com",)))

        self.assertEqual(smtp_ssl.call_args.args, ("smtp.example.com", 465))
        self.assertIsInstance(smtp_ssl.call_args.kwargs["context"], ssl.SSLContext)
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["From"], "sender@example.com")

    @mock.patch.dict(
        os.environ,
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "secret",
        },
        clear=True,
    )
    @mock.patch("news_agent.emailer.smtplib.SMTP_SSL")
    @mock.patch("news_agent.emailer.smtplib.SMTP")
    def test_send_digest_email_uses_starttls_on_port_587(self, smtp_plain: mock.Mock, smtp_ssl: mock.Mock) -> None:
        smtp = smtp_plain.return_value.__enter__.return_value

        send_digest_email(self.digest, EmailConfig(recipients=("reader@example.com",)))

        smtp_ssl.assert_not_called()
        self.assertEqual(smtp_plain.call_args.args, ("smtp.example.com", 587))
        self.assertIsInstance(smtp.starttls.call_args.kwargs["context"], ssl.SSLContext)
        smtp.login.assert_called_once_with("sender@example.com", "secret")
        message = smtp.send_message.call_args.args[0]
        self.assertIsNotNone(message["Date"])
        self.assertIsNotNone(message["Message-ID"])

    @mock.patch.dict(
        os.environ,
        {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "2525",
            "SMTP_SECURITY": "STARTTLS",
            "SMTP_USERNAME": "sender@example.com",
            "SMTP_PASSWORD": "secret",
        },
        clear=True,
    )
    @mock.patch("news_agent.emailer.smtplib.SMTP")
    def test_smtp_security_env_overrides_port_default(self, smtp_plain: mock.Mock) -> None:
        send_digest_email(self.digest, EmailConfig(recipients=("reader@example.com",)))

        self.assertEqual(smtp_plain.call_args.args, ("smtp.example.com", 2525))


if __name__ == "__main__":
    unittest.main()
