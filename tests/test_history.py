from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from news_agent.collector import ArticleCandidate
from news_agent.digest import DigestArticle
from news_agent.history import SentHistory
from news_agent.history import default_history_path


NOW = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)


def _candidate(title: str, url: str) -> ArticleCandidate:
    return ArticleCandidate(index=1, title=title, source="Example", url=url, published_at=None, summary="")


def _article(title: str, url: str) -> DigestArticle:
    return DigestArticle(title=title, source="Example", url=url, summary="", why_it_matters="")


class SentHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state" / "history.json"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_missing_file_starts_empty(self) -> None:
        history = SentHistory.load(self.path, days=7, now=NOW)

        self.assertEqual(history.articles, [])
        self.assertFalse(history.contains(_candidate("Anything", "https://example.com/a")))

    def test_record_save_and_reload_matches_by_url_or_title(self) -> None:
        history = SentHistory.load(self.path, days=7, now=NOW)
        history.record([_article("Big AI news", "https://example.com/story?utm_source=email")], sent_at=NOW)
        history.save()

        reloaded = SentHistory.load(self.path, days=7, now=NOW)

        self.assertTrue(reloaded.contains(_candidate("Different headline", "https://example.com/story/")))
        self.assertTrue(reloaded.contains(_candidate("Big AI News!", "https://other.example.com/copy")))
        self.assertFalse(reloaded.contains(_candidate("Unrelated", "https://example.com/other")))

    def test_old_entries_are_pruned_on_load(self) -> None:
        history = SentHistory(self.path)
        history.record([_article("Old story", "https://example.com/old")], sent_at=NOW - timedelta(days=10))
        history.record([_article("New story", "https://example.com/new")], sent_at=NOW - timedelta(days=1))
        history.save()

        reloaded = SentHistory.load(self.path, days=7, now=NOW)

        self.assertEqual([article.title for article in reloaded.articles], ["New story"])

    def test_corrupt_file_is_ignored_with_warning(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertLogs("news_agent.history", level="WARNING"):
            history = SentHistory.load(self.path, days=7, now=NOW)

        self.assertEqual(history.articles, [])

    def test_saved_file_is_readable_json(self) -> None:
        history = SentHistory(self.path)
        history.record([_article("Story", "https://example.com/s")], sent_at=NOW)
        history.save()

        payload = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["articles"][0]["sent_at"], NOW.isoformat())

    def test_default_history_path_is_per_config(self) -> None:
        self.assertEqual(
            default_history_path(Path("config/personal_topics/work.toml")),
            Path(".agent-state/work-history.json"),
        )


if __name__ == "__main__":
    unittest.main()
