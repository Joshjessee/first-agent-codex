from __future__ import annotations

from email.message import EmailMessage
import os
from unittest import mock
import unittest

from news_agent.collector import ArticleCandidate
from news_agent.collector import _collect_gmail_candidates
from news_agent.collector import collect_candidates
from news_agent.config import AgentConfig
from news_agent.config import GmailSourceConfig
from news_agent.config import GoogleNewsSourceConfig
from news_agent.config import SourcesConfig


class CollectorTests(unittest.TestCase):
    def test_collect_candidates_merges_sources_dedupes_and_reindexes(self) -> None:
        config = AgentConfig(
            topic="AI",
            sources=SourcesConfig(
                google_news=GoogleNewsSourceConfig(enabled=True),
                gmail=GmailSourceConfig(enabled=True),
            ),
        )
        rss_candidate = ArticleCandidate(
            index=1,
            title="Shared headline",
            source="Example News",
            url="https://example.com/shared?utm_source=rss",
            published_at=None,
            summary="RSS summary",
        )
        duplicate_newsletter_candidate = ArticleCandidate(
            index=1,
            title="Shared headline",
            source="Newsletter: Example",
            url="https://example.com/shared?utm_campaign=email",
            published_at=None,
            summary="Newsletter summary",
        )
        newsletter_candidate = ArticleCandidate(
            index=2,
            title="Newsletter-only headline",
            source="Newsletter: Example",
            url="https://example.com/newsletter-only",
            published_at=None,
            summary="Newsletter summary",
        )

        with (
            mock.patch("news_agent.collector._collect_google_news_candidates", return_value=[rss_candidate]),
            mock.patch(
                "news_agent.collector._collect_gmail_candidates",
                return_value=[duplicate_newsletter_candidate, newsletter_candidate],
            ),
        ):
            candidates = collect_candidates(config)

        self.assertEqual([candidate.index for candidate in candidates], [1, 2])
        self.assertEqual([candidate.title for candidate in candidates], ["Shared headline", "Newsletter-only headline"])
        self.assertEqual(candidates[0].source, "Example News")
        self.assertEqual(candidates[1].source, "Newsletter: Example")

    @mock.patch.dict(
        os.environ,
        {
            "GMAIL_USERNAME": "reader@example.com",
            "GMAIL_PASSWORD": "app-password",
        },
        clear=True,
    )
    @mock.patch("news_agent.collector.imaplib.IMAP4_SSL")
    def test_gmail_source_reads_recent_messages_from_selected_sender(
        self,
        imap_ssl: mock.Mock,
    ) -> None:
        raw_message = _newsletter_message(
            html="""
            <a href="https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Farticle%3Futm_source%3Dnewsletter">
              Major AI lab ships a new model
            </a>
            <a href="https://example.com/unsubscribe">Unsubscribe</a>
            """
        )
        mailbox = _FakeMailbox(raw_message.as_bytes())
        imap_ssl.return_value = mailbox
        config = AgentConfig(
            topic="AI",
            sources=SourcesConfig(
                gmail=GmailSourceConfig(
                    enabled=True,
                    mode="senders",
                    senders=("newsletter@example.com",),
                    max_messages=5,
                    max_links_per_message=3,
                )
            ),
        )

        candidates = _collect_gmail_candidates(config)

        imap_ssl.assert_called_once_with("imap.gmail.com", 993)
        self.assertEqual(mailbox.selected_mailboxes, ["INBOX"])
        self.assertIn(("SINCE", mock.ANY, "FROM", '"newsletter@example.com"'), mailbox.searches)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "Major AI lab ships a new model")
        self.assertEqual(candidates[0].source, "Newsletter: AI Briefing")
        self.assertEqual(candidates[0].url, "https://example.com/article")


class _FakeMailbox:
    def __init__(self, raw_message: bytes) -> None:
        self.raw_message = raw_message
        self.selected_mailboxes: list[str] = []
        self.searches: list[tuple[object, ...]] = []

    def __enter__(self) -> "_FakeMailbox":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def login(self, username: str, password: str) -> tuple[str, list[bytes]]:
        return "OK", []

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected_mailboxes.append(mailbox)
        return "OK", []

    def search(self, charset: object, *criteria: object) -> tuple[str, list[bytes]]:
        self.searches.append(criteria)
        return "OK", [b"1"]

    def fetch(self, message_id: bytes, message_parts: str) -> tuple[str, list[tuple[bytes, bytes]]]:
        return "OK", [(b"RFC822", self.raw_message)]


def _newsletter_message(*, html: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Today in AI"
    message["From"] = "AI Briefing <newsletter@example.com>"
    message["Date"] = "Thu, 14 May 2026 08:00:00 +0000"
    message.set_content("Plain fallback")
    message.add_alternative(html, subtype="html")
    return message


if __name__ == "__main__":
    unittest.main()
