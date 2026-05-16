from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from email.utils import parseaddr
from html.parser import HTMLParser
import imaplib
import os
import re
from typing import Iterable
from urllib.parse import parse_qs
from urllib.parse import quote_plus
from urllib.parse import unquote
from urllib.parse import urldefrag
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse

import feedparser

from news_agent.config import AgentConfig


@dataclass(frozen=True)
class ArticleCandidate:
    index: int
    title: str
    source: str
    url: str
    published_at: datetime | None
    summary: str


def collect_candidates(config: AgentConfig, limit: int = 25) -> list[ArticleCandidate]:
    candidates: list[ArticleCandidate] = []
    if config.sources.google_news.enabled:
        candidates.extend(_collect_google_news_candidates(config))
    if config.sources.gmail.enabled:
        candidates.extend(_collect_gmail_candidates(config))

    recent = _filter_recent(candidates, config.lookback_hours)
    return _dedupe(recent)[:limit]


def _collect_google_news_candidates(config: AgentConfig) -> list[ArticleCandidate]:
    query = quote_plus(f"{config.topic} when:1d")
    feed_url = (
        "https://news.google.com/rss/search"
        f"?q={query}&hl={config.language}&gl={config.region}&ceid={config.region}:en"
    )
    feed = feedparser.parse(feed_url)
    return _normalize_entries(feed.entries)


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
    with imaplib.IMAP4_SSL(host, port) as mailbox:
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


def _normalize_entries(entries: Iterable[object]) -> list[ArticleCandidate]:
    candidates: list[ArticleCandidate] = []
    for entry in entries:
        title = _clean_text(getattr(entry, "title", ""))
        url = _clean_url(str(getattr(entry, "link", "")).strip())
        summary = _clean_text(getattr(entry, "summary", ""))
        source = _source_name(entry)
        published_at = _published_at(entry)

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


def _source_name(entry: object) -> str:
    source = getattr(entry, "source", None)
    if source and getattr(source, "title", None):
        return str(source.title).strip()
    return "Unknown source"


def _published_at(entry: object) -> datetime | None:
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
        title_key = candidate.title.lower().strip()
        url_key = _dedupe_url_key(candidate.url)
        if title_key in seen_titles or (url_key and url_key in seen_urls):
            continue
        seen_titles.add(title_key)
        if url_key:
            seen_urls.add(url_key)
        deduped.append(candidate)
    return [
        ArticleCandidate(
            index=index,
            title=candidate.title,
            source=candidate.source,
            url=candidate.url,
            published_at=candidate.published_at,
            summary=candidate.summary,
        )
        for index, candidate in enumerate(deduped, start=1)
    ]


def _dedupe_url_key(url: str) -> str:
    clean_url = _clean_url(url)
    if not clean_url:
        return ""
    parsed = urlparse(clean_url)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), path=path))


def _clean_text(value: str) -> str:
    return " ".join(str(value).replace("\n", " ").split())
