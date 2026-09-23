"""Remembers which articles were already emailed so digests do not repeat them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Iterable

from news_agent.collector import ArticleCandidate
from news_agent.collector import title_key_for
from news_agent.collector import url_key_for
from news_agent.digest import DigestArticle


logger = logging.getLogger(__name__)

HISTORY_DIR = Path(".agent-state")


@dataclass(frozen=True)
class SentArticle:
    title: str
    url: str
    sent_at: datetime


class SentHistory:
    def __init__(self, path: Path, articles: Iterable[SentArticle] = ()) -> None:
        self.path = path
        self.articles = list(articles)
        self._rebuild_keys()

    @classmethod
    def load(cls, path: Path, *, days: int, now: datetime | None = None) -> "SentHistory":
        """Read the history file, keeping only articles sent in the last `days` days.

        A missing or unreadable file starts a fresh history instead of failing
        the run: the worst case is repeating a story, not missing a digest.
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        articles: list[SentArticle] = []
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for item in raw.get("articles", []):
                    sent_at = datetime.fromisoformat(str(item["sent_at"]))
                    if sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=timezone.utc)
                    if sent_at >= cutoff:
                        articles.append(
                            SentArticle(title=str(item.get("title", "")), url=str(item.get("url", "")), sent_at=sent_at)
                        )
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                logger.warning("Ignoring unreadable history file %s: %s", path, error)
                articles = []
        return cls(path, articles)

    def contains(self, candidate: ArticleCandidate) -> bool:
        url_key = url_key_for(candidate.url)
        if url_key and url_key in self._url_keys:
            return True
        return title_key_for(candidate.title) in self._title_keys

    def record(self, articles: Iterable[DigestArticle], *, sent_at: datetime | None = None) -> None:
        sent_at = sent_at or datetime.now(timezone.utc)
        for article in articles:
            self.articles.append(SentArticle(title=article.title, url=article.url, sent_at=sent_at))
        self._rebuild_keys()

    def save(self) -> None:
        payload = {
            "version": 1,
            "articles": [
                {"title": article.title, "url": article.url, "sent_at": article.sent_at.isoformat()}
                for article in self.articles
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file first so a crash never leaves half a file behind.
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(self.path)

    def _rebuild_keys(self) -> None:
        self._url_keys = {key for key in (url_key_for(article.url) for article in self.articles) if key}
        self._title_keys = {key for key in (title_key_for(article.title) for article in self.articles) if key}


def default_history_path(config_path: Path) -> Path:
    """Keep one history file per personal config, e.g. .agent-state/default-history.json."""
    return HISTORY_DIR / f"{config_path.stem}-history.json"
