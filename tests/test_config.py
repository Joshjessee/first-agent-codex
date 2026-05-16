from io import BytesIO
from pathlib import Path
import unittest

from news_agent.config import AgentConfig
from news_agent.config import EmailConfig
from news_agent.config import GmailSourceConfig
from news_agent.config import ScheduleConfig
from news_agent.config import SourcesConfig
from news_agent.config import load_config
from news_agent.config import write_config


class _ReadablePath:
    def __init__(self, content: str) -> None:
        self.content = content.encode("utf-8")

    def open(self, mode: str) -> BytesIO:
        return BytesIO(self.content)

    def __str__(self) -> str:
        return "<memory>"


class _NonClosingBytesIO(BytesIO):
    def close(self) -> None:
        pass


class _WritableParent:
    def mkdir(self, *, parents: bool, exist_ok: bool) -> None:
        return None


class _WritablePath:
    def __init__(self) -> None:
        self.buffer = _NonClosingBytesIO()
        self.parent = _WritableParent()

    def open(self, mode: str) -> _NonClosingBytesIO:
        self.buffer = _NonClosingBytesIO()
        return self.buffer


class ConfigTests(unittest.TestCase):
    def test_load_config_keeps_topic_as_primary_customization(self) -> None:
        config = load_config(Path("config/topics/ai.toml"))

        self.assertEqual(config.topic, "artificial intelligence")
        self.assertEqual(config.article_count, 3)
        self.assertEqual(config.email.subject_prefix, "Daily Research Digest")
        self.assertEqual(config.email.recipients, ("recipient@example.com",))
        self.assertEqual(config.schedule.frequency, "daily")
        self.assertEqual(config.schedule.time, "10:00")
        self.assertTrue(config.sources.google_news.enabled)
        self.assertFalse(config.sources.gmail.enabled)

    def test_load_config_uses_schedule_defaults_when_omitted(self) -> None:
        path = _ReadablePath('topic = "finance"\n')

        config = load_config(path)

        self.assertEqual(config.schedule.frequency, "daily")
        self.assertEqual(config.schedule.time, "09:00")
        self.assertEqual(config.email.recipients, ())
        self.assertTrue(config.sources.google_news.enabled)
        self.assertFalse(config.sources.gmail.enabled)

    def test_load_config_reads_gmail_source_settings(self) -> None:
        path = _ReadablePath(
            """
            topic = "AI"

            [sources.google_news]
            enabled = true

            [sources.gmail]
            enabled = true
            mode = "senders_and_labels"
            senders = ["newsletter@example.com", "briefing@example.com"]
            labels = ["Newsletters", "AI"]
            max_messages = 12
            max_links_per_message = 4
            """
        )

        config = load_config(path)

        self.assertTrue(config.sources.gmail.enabled)
        self.assertEqual(config.sources.gmail.mode, "senders_and_labels")
        self.assertEqual(config.sources.gmail.senders, ("newsletter@example.com", "briefing@example.com"))
        self.assertEqual(config.sources.gmail.labels, ("Newsletters", "AI"))
        self.assertEqual(config.sources.gmail.max_messages, 12)
        self.assertEqual(config.sources.gmail.max_links_per_message, 4)

    def test_write_config_round_trips_user_editable_settings(self) -> None:
        path = _WritablePath()
        write_config(
            path,
            AgentConfig(
                topic="space",
                article_count=5,
                email=EmailConfig(
                    subject_prefix="Space Digest",
                    recipients=("a@example.com", "b@example.com"),
                ),
                schedule=ScheduleConfig(frequency="weekdays", time="08:30"),
                sources=SourcesConfig(
                    gmail=GmailSourceConfig(
                        enabled=True,
                        mode="labels",
                        labels=("Newsletters",),
                        max_messages=8,
                        max_links_per_message=3,
                    )
                ),
            ),
        )

        config = load_config(_ReadablePath(path.buffer.getvalue().decode("utf-8")))

        self.assertEqual(config.topic, "space")
        self.assertEqual(config.article_count, 5)
        self.assertEqual(config.email.subject_prefix, "Space Digest")
        self.assertEqual(config.email.recipients, ("a@example.com", "b@example.com"))
        self.assertEqual(config.schedule.frequency, "weekdays")
        self.assertEqual(config.schedule.time, "08:30")
        self.assertTrue(config.sources.gmail.enabled)
        self.assertEqual(config.sources.gmail.mode, "labels")
        self.assertEqual(config.sources.gmail.labels, ("Newsletters",))
        self.assertEqual(config.sources.gmail.max_messages, 8)
        self.assertEqual(config.sources.gmail.max_links_per_message, 3)


if __name__ == "__main__":
    unittest.main()
