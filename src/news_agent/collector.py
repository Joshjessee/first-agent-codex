from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from email.utils import parseaddr
from html.parser import HTMLParser
import imaplib
from itertools import zip_longest
import logging
import math
import os
import re
import ssl
from typing import Callable
from typing import Iterable
from urllib.parse import parse_qs
from urllib.parse import quote_plus
from urllib.parse import unquote
from urllib.parse import urldefrag
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse
from urllib.request import Request
from urllib.request import urlopen

import feedparser

from news_agent.config import AgentConfig


logger = logging.getLogger(__name__)

FEED_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 (compatible; daily-research-agent/0.2)"


@dataclass(frozen=True)
class ArticleCandidate:
    index: int
    title: str
    source: str
    url: str
    published_at: datetime | None
    summary: str


class NoSourcesAvailableError(RuntimeError):
    """Raised when every enabled source failed or no source is enabled."""


def collect_candidates(
    config: AgentConfig,
    limit: int | None = None,
    *,
    skip: Callable[[ArticleCandidate], bool] | None = None,
) -> list[ArticleCandidate]:
    """Gather, filter, and dedupe article candidates from every enabled source.

    Each source is collected independently, so one broken source (for example a
    Gmail login failure) only logs a warning instead of stopping the whole run.
    Candidates are interleaved across sources so a large source like Google News
    cannot crowd smaller ones out of the final candidate list.
    """
    limit = config.max_candidates if limit is None else limit
    groups, errors = _collect_source_groups(config)
    if not groups:
        if errors:
            raise NoSourcesAvailableError("Every enabled source failed: " + "; ".join(errors))
        raise NoSourcesAvailableError("No article sources are enabled in the config.")

    exclude_pattern = _exclude_pattern(config.exclude_keywords)
    filtered_groups = []
    for group in groups:
        group = _filter_recent(group, config.lookback_hours)
        if exclude_pattern is not None:
            group = [candidate for candidate in group if not _is_excluded(candidate, exclude_pattern)]
        if skip is not None:
            group = [candidate for candidate in group if not skip(candidate)]
        filtered_groups.append(group)

    return _dedupe(_interleave(filtered_groups))[:limit]


def _collect_source_groups(config: AgentConfig) -> tuple[list[list[ArticleCandidate]], list[str]]:
    groups: list[list[ArticleCandidate]] = []
    errors: list[str] = []

    def run_source(name: str, collect: Callable[[], list[list[ArticleCandidate]]]) -> None:
        try:
            source_groups = collect()
        except Exception as error:  # noqa: BLE001 - one bad source must not stop the others.
            logger.warning("Skipping %s source: %s", name, error)
            errors.append(f"{name}: {error}")
            return
        count = sum(len(group) for group in source_groups)
        logger.info("Collected %d candidates from %s.", count, name)
        groups.extend(source_groups)

    if config.sources.google_news.enabled:
        run_source("Google News", lambda: [_collect_google_news_candidates(config)])
    if config.sources.rss.enabled:
        run_source("RSS feeds", lambda: _collect_rss_candidates(config))
    if config.sources.gmail.enabled:
        run_source("Gmail", lambda: [_collect_gmail_candidates(config)])
    return groups, errors


def _collect_google_news_candidates(config: AgentConfig) -> list[ArticleCandidate]:
    feed = _fetch_feed(google_news_feed_url(config))
    return _normalize_entries(feed.entries, strip_source_suffix=True)


def google_news_feed_url(config: AgentConfig) -> str:
    # Google News only understands whole days in the `when:` operator, so round
    # up and let _filter_recent apply the exact hour cutoff afterwards.
    days = max(1, math.ceil(config.lookback_hours / 24))
    query = quote_plus(f"{config.topic} when:{days}d")
    language_code = config.language.split("-", 1)[0] or "en"
    return (
        "https://news.google.com/rss/search"
        f"?q={query}&hl={config.language}&gl={config.region}&ceid={config.region}:{language_code}"
    )


def _collect_rss_candidates(config: AgentConfig) -> list[list[ArticleCandidate]]:
    rss_config = config.sources.rss
    if not rss_config.feeds:
        raise RuntimeError("RSS source is enabled, but no feeds are listed in sources.rss.feeds.")

    groups: list[list[ArticleCandidate]] = []
    errors: list[str] = []
    for feed_url in rss_config.feeds:
        try:
            feed = _fetch_feed(feed_url)
        except Exception as error:  # noqa: BLE001 - keep going with the other feeds.
            logger.warning("Skipping RSS feed %s: %s", feed_url, error)
            errors.append(f"{feed_url}: {error}")
            continue
        feed_title = _clean_text(getattr(getattr(feed, "feed", None), "title", "") or "")
        entries = list(feed.entries)[: rss_config.max_items_per_feed]
        groups.append(_normalize_entries(entries, default_source=feed_title or urlparse(feed_url).netloc))

    if not groups:
        raise RuntimeError("Every RSS feed failed: " + "; ".join(errors))
    return groups


def _fetch_feed(url: str) -> feedparser.FeedParserDict:
    """Download a feed with a timeout so a slow server cannot hang the agent."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=FEED_TIMEOUT_SECONDS) as response:
        data = response.read()
    feed = feedparser.parse(data)
    if feed.get("bozo") and not feed.entries:
        raise RuntimeError(f"Could not parse feed {url}: {feed.get('bozo_exception')}")
    return feed


def _collect_gmail_candidates(config: AgentConfig) -> list[ArticleCandidate]:
    username = os.getenv("GMAIL_USERNAME") or os.getenv("SMTP_USERNAME") or os.getenv("EMAIL_FROM")
    password = os.getenv("GMAIL_PASSWORD") or os.getenv("SMTP_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "Gmail source is enabled, but GMAIL_USERNAME/GMAIL_PASSWORD or "
            "SMTP_USERNAME/SMTP_PASSWORD are not configured."
        )

    host = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com")
    port = int(os.getenv("GMAIL_IMAP_PORT") or "993")
    gmail_config = config.sources.gmail
    mailboxes = _gmail_mailboxes(gmail_config.mode, gmail_config.labels)
    since = datetime.now(timezone.utc) - timedelta(hours=config.lookback_hours)

    messages: list[Message] = []
    # An explicit default context makes Python verify the server certificate.
    with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=FEED_TIMEOUT_SECONDS) as mailbox:
        mailbox.login(username, password)
        for mail_label in mailboxes:
            if len(messages) >= gmail_config.max_messages:
                break
            status, _ = mailbox.select(mail_label, readonly=True)
            if status != "OK":
                continue
            message_ids = _search_gmail_message_ids(
                mailbox,
                mode=gmail_config.mode,
                senders=gmail_config.senders,
                since=since,
            )
            for message_id in message_ids:
                if len(messages) >= gmail_config.max_messages:
                    break
                message = _fetch_gmail_message(mailbox, message_id)
                if message is not None:
                    messages.append(message)

    candidates: list[ArticleCandidate] = []
    for message in messages:
        candidates.extend(
            _extract_newsletter_candidates_from_message(
                message,
                max_links=gmail_config.max_links_per_message,
            )
        )
    return candidates


def _gmail_mailboxes(mode: str, labels: tuple[str, ...]) -> tuple[str, ...]:
    if mode == "senders":
        return ("INBOX",)
    return labels


def _search_gmail_message_ids(
    mailbox: imaplib.IMAP4_SSL,
    *,
    mode: str,
    senders: tuple[str, ...],
    since: datetime,
) -> list[bytes]:
    since_text = since.strftime("%d-%b-%Y")
    if mode == "labels":
        status, data = mailbox.search(None, "SINCE", since_text)
        return _parse_message_ids(status, data)

    message_ids: list[bytes] = []
    seen: set[bytes] = set()
    for sender in senders:
        status, data = mailbox.search(None, "SINCE", since_text, "FROM", f'"{sender}"')
        for message_id in _parse_message_ids(status, data):
            if message_id in seen:
                continue
            seen.add(message_id)
            message_ids.append(message_id)
    return message_ids


def _parse_message_ids(status: str, data: list[bytes]) -> list[bytes]:
    if status != "OK" or not data:
        return []
    return [message_id for message_id in data[0].split() if message_id]


def _fetch_gmail_message(mailbox: imaplib.IMAP4_SSL, message_id: bytes) -> Message | None:
    status, data = mailbox.fetch(message_id, "(RFC822)")
    if status != "OK":
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
            return BytesParser(policy=policy.default).parsebytes(item[1])
    return None


def _extract_newsletter_candidates_from_message(
    message: Message,
    *,
    max_links: int,
) -> list[ArticleCandidate]:
    subject = _clean_text(str(message.get("subject", "")))
    sender = _newsletter_sender(message)
    published_at = _message_date(message)
    links = _extract_message_links(message)

    candidates: list[ArticleCandidate] = []
    for title, url in links:
        clean_url = _clean_url(url)
        clean_title = _clean_link_title(title, clean_url)
        if not clean_title or not clean_url or _is_low_value_link(clean_title, clean_url):
            continue
        candidates.append(
            ArticleCandidate(
                index=len(candidates) + 1,
                title=clean_title,
                source=f"Newsletter: {sender}",
                url=clean_url,
                published_at=published_at,
                summary=f"Linked from newsletter email: {subject}" if subject else "Linked from newsletter email.",
            )
        )
        if len(candidates) == max_links:
            break
    return candidates


def _normalize_entries(
    entries: Iterable[object],
    *,
    default_source: str = "Unknown source",
    strip_source_suffix: bool = False,
) -> list[ArticleCandidate]:
    candidates: list[ArticleCandidate] = []
    for entry in entries:
        title = _clean_text(_strip_html(getattr(entry, "title", "")))
        url = _clean_url(str(getattr(entry, "link", "")).strip())
        source = _source_name(entry, default=default_source)
        summary = _clean_text(_strip_html(getattr(entry, "summary", "")))
        published_at = _published_at(entry)

        if strip_source_suffix:
            title = _strip_source_suffix(title, source)
        if summary.lower().startswith(title.lower()):
            # Google News snippets just repeat the headline and source name.
            summary = ""

        if not title or not url:
            continue

        candidates.append(
            ArticleCandidate(
                index=len(candidates) + 1,
                title=title,
                source=source,
                url=url,
                published_at=published_at,
                summary=summary,
            )
        )
    return candidates


def _strip_source_suffix(title: str, source: str) -> str:
    """Turn Google News titles like "Big news - Example Times" into "Big news"."""
    suffix = f" - {source}"
    if source and title.endswith(suffix) and len(title) > len(suffix):
        return title[: -len(suffix)].strip()
    return title


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _strip_html(value: object) -> str:
    text = str(value or "")
    if "<" not in text and "&" not in text:
        return text
    extractor = _TextExtractor()
    extractor.feed(text)
    extractor.close()
    return " ".join(extractor.parts)


def _exclude_pattern(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    if not keywords:
        return None
    alternatives = "|".join(re.escape(keyword) for keyword in keywords)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def _is_excluded(candidate: ArticleCandidate, pattern: re.Pattern[str]) -> bool:
    return bool(pattern.search(f"{candidate.title} {candidate.summary} {candidate.source}"))


def _interleave(groups: list[list[ArticleCandidate]]) -> list[ArticleCandidate]:
    return [candidate for round_ in zip_longest(*groups) for candidate in round_ if candidate is not None]


def _newsletter_sender(message: Message) -> str:
    name, address = parseaddr(str(message.get("from", "")))
    sender = name or address
    return _clean_text(sender) or "Gmail newsletter"


def _message_date(message: Message) -> datetime | None:
    value = message.get("date")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_message_links(message: Message) -> list[tuple[str, str]]:
    html_parts: list[str] = []
    text_parts: list[str] = []

    if message.is_multipart():
        parts = list(message.walk())
    else:
        parts = [message]

    for part in parts:
        content_type = part.get_content_type()
        if content_type not in {"text/html", "text/plain"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            charset = part.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")

        if content_type == "text/html":
            html_parts.append(str(content))
        else:
            text_parts.append(str(content))

    links: list[tuple[str, str]] = []
    for html in html_parts:
        extractor = _NewsletterLinkParser()
        extractor.feed(html)
        links.extend(extractor.links)

    for text in text_parts:
        links.extend(_extract_plain_text_links(text))

    return links


class _NewsletterLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        attrs_by_name = {name.lower(): value for name, value in attrs if value is not None}
        href = attrs_by_name.get("href")
        if href:
            self._href = href
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        self.links.append((" ".join(self._text_parts), self._href))
        self._href = None
        self._text_parts = []


def _extract_plain_text_links(text: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    markdown_pattern = re.compile(r"\[([^\]]{8,220})\]\((https?://[^)\s]+)\)")
    for title, url in markdown_pattern.findall(text):
        links.append((title, url))

    url_pattern = re.compile(r"https?://[^\s<>)\"']+")
    for match in url_pattern.finditer(text):
        url = match.group(0).rstrip(".,;:")
        if any(existing_url == url for _, existing_url in links):
            continue
        links.append((urlparse(url).netloc, url))
    return links


def _clean_link_title(title: str, url: str) -> str:
    text = _clean_text(title)
    generic_titles = {
        "read more",
        "read more.",
        "continue reading",
        "learn more",
        "view online",
        "open",
        "click here",
        "here",
    }
    if len(text) < 8 or text.lower() in generic_titles:
        return _clean_text(unquote(urlparse(url).path.rsplit("/", 1)[-1].replace("-", " ")))
    return text


def _clean_url(url: str) -> str:
    text = str(url).strip()
    if not text:
        return ""
    text, _ = urldefrag(text)
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    query = parse_qs(parsed.query)
    redirect_target = None
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        redirect_target = query.get("q", [""])[0]
    elif "url" in query and any(host in parsed.netloc for host in ("safelinks", "sendgrid", "mailchi.mp")):
        redirect_target = query.get("url", [""])[0]
    if redirect_target:
        return _clean_url(redirect_target)

    filtered_query = [
        (key, value)
        for key, values in query.items()
        for value in values
        if not key.lower().startswith("utm_")
        and key.lower() not in {"mc_cid", "mc_eid", "fbclid", "gclid"}
    ]
    normalized = parsed._replace(
        netloc=parsed.netloc.lower(),
        query=urlencode(filtered_query, doseq=True),
    )
    return urlunparse(normalized)


def _is_low_value_link(title: str, url: str) -> bool:
    parsed = urlparse(url)
    text = f"{title} {url}".lower()
    if parsed.path.lower().endswith((".gif", ".jpg", ".jpeg", ".png", ".webp", ".svg", ".pdf")):
        return True
    low_value_terms = (
        "unsubscribe",
        "manage preferences",
        "privacy policy",
        "terms of service",
        "advertise",
        "sponsor",
        "view in browser",
        "view this email",
    )
    return any(term in text for term in low_value_terms)


def _source_name(entry: object, *, default: str = "Unknown source") -> str:
    source = getattr(entry, "source", None)
    if source and getattr(source, "title", None):
        return str(source.title).strip()
    return default


def _published_at(entry: object) -> datetime | None:
    # feedparser pre-parses both RSS (RFC 822) and Atom (ISO 8601) dates into
    # UTC struct_time values, which is more reliable than parsing strings.
    for attribute in ("published_parsed", "updated_parsed"):
        parsed_time = getattr(entry, attribute, None)
        if parsed_time:
            try:
                return datetime(*parsed_time[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue

    published = getattr(entry, "published", None)
    if not published:
        return None
    try:
        parsed = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _filter_recent(candidates: list[ArticleCandidate], lookback_hours: int) -> list[ArticleCandidate]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    return [
        candidate
        for candidate in candidates
        if candidate.published_at is None or candidate.published_at >= cutoff
    ]


def _dedupe(candidates: list[ArticleCandidate]) -> list[ArticleCandidate]:
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    deduped: list[ArticleCandidate] = []
    for candidate in candidates:
        title_key = title_key_for(candidate.title)
        url_key = url_key_for(candidate.url)
        if title_key in seen_titles or (url_key and url_key in seen_urls):
            continue
        seen_titles.add(title_key)
        if url_key:
            seen_urls.add(url_key)
        deduped.append(candidate)
    return [replace(candidate, index=index) for index, candidate in enumerate(deduped, start=1)]


def title_key_for(title: str) -> str:
    """A normalized headline used to spot the same story from different links."""
    return " ".join(re.sub(r"[^\w\s]", " ", title.lower()).split())


def url_key_for(url: str) -> str:
    """A normalized URL used to spot the same article behind tracking links."""
    clean_url = _clean_url(url)
    if not clean_url:
        return ""
    parsed = urlparse(clean_url)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), path=path))


def _clean_text(value: str) -> str:
    return " ".join(str(value).replace("\n", " ").split())
