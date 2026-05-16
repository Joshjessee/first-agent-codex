from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class EmailConfig:
    subject_prefix: str = "Daily Research Digest"
    recipients: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScheduleConfig:
    frequency: str = "daily"
    time: str = "09:00"


@dataclass(frozen=True)
class GoogleNewsSourceConfig:
    enabled: bool = True


@dataclass(frozen=True)
class GmailSourceConfig:
    enabled: bool = False
    mode: str = "senders"
    senders: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    max_messages: int = 25
    max_links_per_message: int = 6


@dataclass(frozen=True)
class SourcesConfig:
    google_news: GoogleNewsSourceConfig = field(default_factory=GoogleNewsSourceConfig)
    gmail: GmailSourceConfig = field(default_factory=GmailSourceConfig)


@dataclass(frozen=True)
class AgentConfig:
    topic: str
    article_count: int = 3
    lookback_hours: int = 30
    language: str = "en-US"
    region: str = "US"
    email: EmailConfig = field(default_factory=EmailConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)


def load_config(path: Path) -> AgentConfig:
    with path.open("rb") as config_file:
        raw = tomllib.load(config_file)

    topic = str(raw.get("topic", "")).strip()
    if not topic:
        raise ValueError(f"Config file {path} must define a non-empty topic.")

    email_value = raw.get("email", {})
    email_raw = email_value if isinstance(email_value, dict) else {}
    return AgentConfig(
        topic=topic,
        article_count=int(raw.get("article_count", 3)),
        lookback_hours=int(raw.get("lookback_hours", 30)),
        language=str(raw.get("language", "en-US")),
        region=str(raw.get("region", "US")),
        email=EmailConfig(
            subject_prefix=str(email_raw.get("subject_prefix", "Daily Research Digest")),
            recipients=_coerce_recipients(email_raw.get("recipients", ())),
        ),
        schedule=_load_schedule(raw.get("schedule", {})),
        sources=_load_sources(raw.get("sources", {})),
    )


def write_config(path: Path, config: AgentConfig) -> None:
    import tomli_w

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "topic": config.topic,
        "article_count": config.article_count,
        "lookback_hours": config.lookback_hours,
        "language": config.language,
        "region": config.region,
        "email": {
            "subject_prefix": config.email.subject_prefix,
            "recipients": list(config.email.recipients),
        },
        "schedule": {
            "frequency": config.schedule.frequency,
            "time": config.schedule.time,
        },
        "sources": {
            "google_news": {
                "enabled": config.sources.google_news.enabled,
            },
            "gmail": {
                "enabled": config.sources.gmail.enabled,
                "mode": config.sources.gmail.mode,
                "senders": list(config.sources.gmail.senders),
                "labels": list(config.sources.gmail.labels),
                "max_messages": config.sources.gmail.max_messages,
                "max_links_per_message": config.sources.gmail.max_links_per_message,
            },
        },
    }
    with path.open("wb") as config_file:
        tomli_w.dump(payload, config_file)


def _coerce_recipients(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_recipients = [value]
    elif isinstance(value, list):
        raw_recipients = value
    elif isinstance(value, tuple):
        raw_recipients = list(value)
    else:
        raw_recipients = []

    recipients = []
    for recipient in raw_recipients:
        text = str(recipient).strip()
        if text:
            recipients.append(text)
    return tuple(recipients)


def _load_schedule(value: object) -> ScheduleConfig:
    schedule_raw = value if isinstance(value, dict) else {}
    frequency = str(schedule_raw.get("frequency", "daily")).strip().lower()
    if frequency not in {"daily", "weekdays"}:
        frequency = "daily"
    return ScheduleConfig(
        frequency=frequency,
        time=str(schedule_raw.get("time", "09:00")).strip() or "09:00",
    )


def _load_sources(value: object) -> SourcesConfig:
    sources_raw = value if isinstance(value, dict) else {}
    google_news_raw = sources_raw.get("google_news", {})
    gmail_raw = sources_raw.get("gmail", {})
    google_news = google_news_raw if isinstance(google_news_raw, dict) else {}
    gmail = gmail_raw if isinstance(gmail_raw, dict) else {}

    mode = str(gmail.get("mode", "senders")).strip().lower()
    if mode not in {"senders", "labels", "senders_and_labels"}:
        mode = "senders"

    return SourcesConfig(
        google_news=GoogleNewsSourceConfig(
            enabled=_coerce_bool(google_news.get("enabled", True), default=True),
        ),
        gmail=GmailSourceConfig(
            enabled=_coerce_bool(gmail.get("enabled", False), default=False),
            mode=mode,
            senders=_coerce_text_tuple(gmail.get("senders", ())),
            labels=_coerce_text_tuple(gmail.get("labels", ())),
            max_messages=max(1, int(gmail.get("max_messages", 25))),
            max_links_per_message=max(1, int(gmail.get("max_links_per_message", 6))),
        ),
    )


def _coerce_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, list):
        raw_values = value
    elif isinstance(value, tuple):
        raw_values = list(value)
    else:
        raw_values = []

    values = []
    for item in raw_values:
        text = str(item).strip()
        if text:
            values.append(text)
    return tuple(values)
