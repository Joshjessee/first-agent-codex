from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import smtplib
import tomllib
from typing import Sequence

from dotenv import load_dotenv
from openai import OpenAIError

from news_agent.collector import ArticleCandidate
from news_agent.collector import collect_candidates
from news_agent.config import load_config
from news_agent.emailer import send_digest_email
from news_agent.history import SentHistory
from news_agent.history import default_history_path
from news_agent.openai_ranker import build_digest


logger = logging.getLogger("news_agent")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Send a daily topic research digest.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/personal_topics/default.toml"),
        help="Path to the topic config file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest instead of sending email.",
    )
    parser.add_argument(
        "--preview-html",
        type=Path,
        metavar="PATH",
        help="Also save the email as an HTML file you can open in a browser.",
    )
    parser.add_argument(
        "--show-candidates",
        action="store_true",
        help="List the collected article candidates and stop before calling OpenAI.",
    )
    parser.add_argument(
        "--history-file",
        type=Path,
        metavar="PATH",
        help="Where to remember already-sent articles (default: .agent-state/<config name>-history.json).",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Ignore previously sent articles for this run and do not record new ones.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress logs.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    # Third-party libraries are chatty at DEBUG level; keep the focus on the agent.
    for noisy_logger in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    load_dotenv()
    try:
        _run(args)
    except tomllib.TOMLDecodeError as error:
        raise SystemExit(f"Error: the config file is not valid TOML: {error}") from error
    except smtplib.SMTPAuthenticationError as error:
        raise SystemExit(
            "Error: the email server rejected the SMTP username/password. "
            "For Gmail, use an app password rather than your normal password."
        ) from error
    except (RuntimeError, ValueError, OpenAIError, smtplib.SMTPException, OSError) as error:
        raise SystemExit(f"Error: {error}") from error


def _run(args: argparse.Namespace) -> None:
    if not args.config.exists():
        raise RuntimeError(
            f"config file not found: {args.config}\n"
            "Create one with `daily-research-agent-settings`, or pass --config config/topics/ai.toml to try the sample."
        )
    config = load_config(args.config)
    logger.info("Loaded config %s (topic: %s).", args.config, config.topic)

    history: SentHistory | None = None
    if config.history.enabled and not args.no_history:
        history_path = args.history_file or default_history_path(args.config)
        history = SentHistory.load(history_path, days=config.history.days)
        logger.info("Skipping %d articles already sent in the last %d days.", len(history.articles), config.history.days)

    candidates = collect_candidates(config, skip=history.contains if history is not None else None)
    logger.info("Found %d unique recent candidates.", len(candidates))

    if args.show_candidates:
        print(_format_candidates(candidates))
        return

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Missing required environment variable: OPENAI_API_KEY")

    digest = build_digest(config, candidates)

    if args.preview_html:
        args.preview_html.parent.mkdir(parents=True, exist_ok=True)
        args.preview_html.write_text(digest.to_html(), encoding="utf-8")
        logger.info("Saved HTML preview to %s.", args.preview_html)

    if args.dry_run:
        print(digest.to_text())
        return

    send_digest_email(digest, config.email)
    print(f"Sent digest for '{config.topic}' with {len(digest.articles)} articles.")

    if history is not None:
        history.record(digest.articles)
        try:
            history.save()
        except OSError as error:
            # The email already went out, so a history problem should not fail the run.
            logger.warning("Could not save sent-article history to %s: %s", history.path, error)


def _format_candidates(candidates: list[ArticleCandidate]) -> str:
    if not candidates:
        return "No candidates found."
    lines = []
    for candidate in candidates:
        published = candidate.published_at.strftime("%Y-%m-%d %H:%M UTC") if candidate.published_at else "undated"
        lines.append(f"{candidate.index:>3}. {candidate.title}")
        lines.append(f"     {candidate.source} | {published}")
        lines.append(f"     {candidate.url}")
    return "\n".join(lines)
