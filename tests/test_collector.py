from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import os
import ssl
from unittest import mock
import unittest

from news_agent.collector import ArticleCandidate
from news_agent.collector import NoSourcesAvailableError
from news_agent.collector import _collect_google_news_candidates
from news_agent.collector import _collect_rss_candidates
from news_agent.collector import google_news_feed_url
from news_agent.collector import _collect_gmail_candidates
from news_agent.collector import collect_candidates
from news_agent.config import AgentConfig
from news_agent.config import GmailSourceConfig
from news_agent.config import GoogleNewsSourceConfig
from news_agent.config import RssSourceConfig
from news_agent.config import SourcesConfig


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: timezone | None = None) -> datetime:
        fixed = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
        if tz is None:
            return fixed.replace(tzinfo=None)
        return fixed.astimezone(tz)


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

    def test_collect_candidates_filters_dated_candidates_by_lookback_and_keeps_undated(self) -> None:
        recent_candidate = ArticleCandidate(
            index=1,
            title="Recent headline",
            source="Example News",
            url="https://example.com/recent",
            published_at=_FixedDatetime.now(timezone.utc) - timedelta(hours=2),
            summary="Recent summary",
        )
        old_candidate = ArticleCandidate(
            index=2,
            title="Old headline",
            source="Example News",
            url="https://example.com/old",
            published_at=_FixedDatetime.now(timezone.utc) - timedelta(hours=8),
            summary="Old summary",
        )
        undated_candidate = ArticleCandidate(
            index=3,
            title="Undated headline",
            source="Example News",
            url="https://example.com/undated",
            published_at=None,
            summary="Undated summary",
        )
        config = AgentConfig(
            topic="AI",
            lookback_hours=6,
            sources=SourcesConfig(
                google_news=GoogleNewsSourceConfig(enabled=True),
                gmail=GmailSourceConfig(enabled=False),
            ),
        )

        with (
            mock.patch("news_agent.collector.datetime", _FixedDatetime),
            mock.patch(
                "news_agent.collector._collect_google_news_candidates",
                return_value=[recent_candidate, old_candidate, undated_candidate],
            ),
        ):
            candidates = collect_candidates(config)

        self.assertEqual([candidate.title for candidate in candidates], ["Recent headline", "Undated headline"])
        self.assertEqual([candidate.index for candidate in candidates], [1, 2])

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
            lookback_hours=72,
        )

        with mock.patch("news_agent.collector.datetime", _FixedDatetime):
            candidates = _collect_gmail_candidates(config)

        self.assertEqual(imap_ssl.call_args.args, ("imap.gmail.com", 993))
        self.assertIsInstance(imap_ssl.call_args.kwargs["ssl_context"], ssl.SSLContext)
        self.assertEqual(mailbox.selected_mailboxes, ["INBOX"])
        self.assertIn(("SINCE", "14-Jun-2026", "FROM", '"newsletter@example.com"'), mailbox.searches)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "Major AI lab ships a new model")
        self.assertEqual(candidates[0].source, "Newsletter: AI Briefing")
        self.assertEqual(candidates[0].url, "https://example.com/article")

    def test_google_news_search_window_follows_lookback_hours(self) -> None:
        self.assertIn("when%3A1d", google_news_feed_url(AgentConfig(topic="AI", lookback_hours=12)))
        self.assertIn("when%3A2d", google_news_feed_url(AgentConfig(topic="AI", lookback_hours=30)))
        self.assertIn("when%3A7d", google_news_feed_url(AgentConfig(topic="AI", lookback_hours=168)))
        url = google_news_feed_url(AgentConfig(topic="AI", language="fr-FR", region="FR"))
        self.assertIn("hl=fr-FR&gl=FR&ceid=FR:fr", url)

    def test_google_news_entries_drop_html_snippets_and_source_suffix(self) -> None:
        with mock.patch("news_agent.collector.urlopen", return_value=_FakeResponse(GOOGLE_NEWS_RSS)):
            candidates = _collect_google_news_candidates(AgentConfig(topic="AI"))

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "Chip maker unveils faster AI accelerator")
        self.assertEqual(candidates[0].source, "Example Times")
        self.assertEqual(candidates[0].summary, "")
        self.assertEqual(candidates[0].published_at, datetime(2026, 6, 17, 9, 30, tzinfo=timezone.utc))

    def test_rss_source_reads_atom_feed_with_iso_dates(self) -> None:
        config = AgentConfig(
            topic="AI",
            sources=SourcesConfig(
                rss=RssSourceConfig(enabled=True, feeds=("https://blog.example.com/feed",), max_items_per_feed=1)
            ),
        )

        with mock.patch("news_agent.collector.urlopen", return_value=_FakeResponse(ATOM_FEED)):
            groups = _collect_rss_candidates(config)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 1)
        candidate = groups[0][0]
        self.assertEqual(candidate.title, "New open model released")
        self.assertEqual(candidate.source, "Example Lab Blog")
        self.assertEqual(candidate.summary, "Weights and a technical report are available.")
        self.assertEqual(candidate.published_at, datetime(2026, 6, 17, 8, 0, tzinfo=timezone.utc))

    def test_rss_source_keeps_working_feeds_when_one_fails(self) -> None:
        config = AgentConfig(
            topic="AI",
            sources=SourcesConfig(
                rss=RssSourceConfig(enabled=True, feeds=("https://broken.example.com/feed", "https://blog.example.com/feed"))
            ),
        )

        def fake_urlopen(request: object, timeout: int) -> _FakeResponse:
            if "broken" in request.full_url:
                raise OSError("connection refused")
            return _FakeResponse(ATOM_FEED)

        with mock.patch("news_agent.collector.urlopen", side_effect=fake_urlopen):
            groups = _collect_rss_candidates(config)

        self.assertEqual(len(groups), 1)

    def test_one_failing_source_does_not_stop_the_others(self) -> None:
        config = AgentConfig(
            topic="AI",
            sources=SourcesConfig(gmail=GmailSourceConfig(enabled=True)),
        )

        with (
            mock.patch("news_agent.collector._collect_google_news_candidates", return_value=[_candidate("Working source")]),
            mock.patch("news_agent.collector._collect_gmail_candidates", side_effect=RuntimeError("login failed")),
            self.assertLogs("news_agent.collector", level="WARNING") as logs,
        ):
            candidates = collect_candidates(config)

        self.assertEqual([candidate.title for candidate in candidates], ["Working source"])
        self.assertIn("login failed", logs.output[0])

    def test_all_sources_failing_raises_clear_error(self) -> None:
        config = AgentConfig(topic="AI")

        with (
            mock.patch("news_agent.collector._collect_google_news_candidates", side_effect=OSError("offline")),
            self.assertLogs("news_agent.collector", level="WARNING"),
            self.assertRaisesRegex(NoSourcesAvailableError, "Google News: offline"),
        ):
            collect_candidates(config)

    def test_no_enabled_sources_raises_clear_error(self) -> None:
        config = AgentConfig(topic="AI", sources=SourcesConfig(google_news=GoogleNewsSourceConfig(enabled=False)))

        with self.assertRaisesRegex(NoSourcesAvailableError, "No article sources are enabled"):
            collect_candidates(config)

    def test_collect_candidates_interleaves_sources_before_limiting(self) -> None:
        config = AgentConfig(
            topic="AI",
            sources=SourcesConfig(gmail=GmailSourceConfig(enabled=True)),
        )
        news = [_candidate(f"News story {number}") for number in range(10)]
        newsletter = [_candidate(f"Newsletter story {number}") for number in range(2)]

        with (
            mock.patch("news_agent.collector._collect_google_news_candidates", return_value=news),
            mock.patch("news_agent.collector._collect_gmail_candidates", return_value=newsletter),
        ):
            candidates = collect_candidates(config, limit=4)

        self.assertEqual(
            [candidate.title for candidate in candidates],
            ["News story 0", "Newsletter story 0", "News story 1", "Newsletter story 1"],
        )

    def test_collect_candidates_applies_exclude_keywords_and_skip(self) -> None:
        config = AgentConfig(topic="AI", exclude_keywords=("crypto", "Rumor Mill"))
        candidates_in = [
            _candidate("Crypto exchange adds AI features"),
            _candidate("Rumor mill: new phone"),
            _candidate("Cryptography research milestone"),
            _candidate("Already sent story"),
            _candidate("Fresh story"),
        ]

        with mock.patch("news_agent.collector._collect_google_news_candidates", return_value=candidates_in):
            candidates = collect_candidates(config, skip=lambda candidate: candidate.title == "Already sent story")

        self.assertEqual(
            [candidate.title for candidate in candidates],
            ["Cryptography research milestone", "Fresh story"],
        )

    def test_dedupe_ignores_headline_punctuation_and_case(self) -> None:
        config = AgentConfig(topic="AI")
        candidates_in = [
            _candidate("OpenAI ships GPT update!", url="https://a.example.com/1"),
            _candidate("openai ships gpt update", url="https://b.example.com/2"),
        ]

        with mock.patch("news_agent.collector._collect_google_news_candidates", return_value=candidates_in):
            candidates = collect_candidates(config)

        self.assertEqual(len(candidates), 1)


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


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self.body = body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _candidate(title: str, *, url: str | None = None) -> ArticleCandidate:
    slug = title.lower().replace(" ", "-")
    return ArticleCandidate(
        index=1,
        title=title,
        source="Example News",
        url=url or f"https://example.com/{slug}",
        published_at=None,
        summary="",
    )


GOOGLE_NEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>AI - Google News</title>
<item>
  <title>Chip maker unveils faster AI accelerator - Example Times</title>
  <link>https://news.google.com/rss/articles/abc123</link>
  <pubDate>Wed, 17 Jun 2026 09:30:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/abc123"&gt;Chip maker unveils faster AI accelerator&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Example Times&lt;/font&gt;</description>
  <source url="https://times.example.com">Example Times</source>
</item>
</channel></rss>
"""

ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Lab Blog</title>
  <entry>
    <title>New open model released</title>
    <link href="https://blog.example.com/new-model?utm_source=rss"/>
    <updated>2026-06-17T08:00:00Z</updated>
    <summary type="html">&lt;p&gt;Weights and a &lt;b&gt;technical report&lt;/b&gt; are available.&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Older post</title>
    <link href="https://blog.example.com/older"/>
    <updated>2026-06-10T08:00:00Z</updated>
  </entry>
</feed>
"""


if __name__ == "__main__":
    unittest.main()
