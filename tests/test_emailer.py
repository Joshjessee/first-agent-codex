from datetime import datetime, timezone
import os
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

        smtp_ssl.assert_called_once_with("smtp.example.com", 465)
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["From"], "sender@example.com")


if __name__ == "__main__":
    unittest.main()
