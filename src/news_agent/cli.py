from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from news_agent.collector import collect_candidates
from news_agent.config import load_config
from news_agent.emailer import send_digest_email
from news_agent.openai_ranker import build_digest


def main() -> None:
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
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Missing required environment variable: OPENAI_API_KEY")

    candidates = collect_candidates(config)
    digest = build_digest(config, candidates)

    if args.dry_run:
        print(digest.to_text())
        return

    send_digest_email(digest, config.email)
    print(f"Sent digest for '{config.topic}' with {len(digest.articles)} articles.")
