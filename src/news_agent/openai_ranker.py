from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re

from openai import OpenAI

from news_agent.collector import ArticleCandidate
from news_agent.config import AgentConfig
from news_agent.digest import Digest, DigestArticle


def build_digest(config: AgentConfig, candidates: list[ArticleCandidate]) -> Digest:
    if not candidates:
        raise RuntimeError(f"No recent article candidates found for topic: {config.topic}")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Missing required environment variable: OPENAI_API_KEY")

    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-5")
    response = client.responses.create(
        model=model,
        instructions=(
            "You are a careful news research assistant. Select the most important, "
            "non-duplicative articles for the requested topic. Favor timely, concrete "
            "news from reputable sources over opinion, rumors, roundups, and minor updates. "
            "Return only valid JSON."
        ),
        input=_build_prompt(config, candidates),
    )

    payload = _parse_json(response.output_text)
    articles = _coerce_articles(payload, candidates, config.article_count)
    return Digest(
        topic=config.topic,
        generated_at=datetime.now(timezone.utc).astimezone(),
        articles=articles,
    )


def _build_prompt(config: AgentConfig, candidates: list[ArticleCandidate]) -> str:
    candidate_lines = []
    for candidate in candidates:
        published = (
            candidate.published_at.isoformat()
            if candidate.published_at is not None
            else "unknown"
        )
        candidate_lines.append(
            "\n".join(
                [
                    f"Index: {candidate.index}",
                    f"Title: {candidate.title}",
                    f"Source: {candidate.source}",
                    f"URL: {candidate.url}",
                    f"Published: {published}",
                    f"Snippet: {candidate.summary}",
                ]
            )
        )

    return f"""
Topic: {config.topic}
Number of articles to select: {config.article_count}

Article candidates:
{chr(10).join(candidate_lines)}

Return JSON in exactly this shape:
{{
  "articles": [
    {{
      "index": 1,
      "summary": "Two or three concise sentences explaining what happened.",
      "why_it_matters": "One concise sentence explaining why this is one of today's biggest stories."
    }}
  ]
}}
""".strip()


def _parse_json(text: str) -> dict[str, object]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"OpenAI returned non-JSON output: {text}") from error
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI JSON output must be an object.")
    return parsed


def _coerce_articles(
    payload: dict[str, object],
    candidates: list[ArticleCandidate],
    article_count: int,
) -> list[DigestArticle]:
    by_index = {candidate.index: candidate for candidate in candidates}
    raw_articles = payload.get("articles")
    if not isinstance(raw_articles, list):
        raise RuntimeError("OpenAI JSON output must contain an articles list.")

    selected: list[DigestArticle] = []
    used_indexes: set[int] = set()
    for raw_article in raw_articles:
        if not isinstance(raw_article, dict):
            continue
        index = int(raw_article.get("index", 0))
        candidate = by_index.get(index)
        if candidate is None or index in used_indexes:
            continue

        used_indexes.add(index)
        selected.append(
            DigestArticle(
                title=candidate.title,
                source=candidate.source,
                url=candidate.url,
                summary=str(raw_article.get("summary", "")).strip(),
                why_it_matters=str(raw_article.get("why_it_matters", "")).strip(),
            )
        )
        if len(selected) == article_count:
            break

    if len(selected) < min(article_count, len(candidates)):
        raise RuntimeError("OpenAI did not select enough valid article indexes.")

    return selected
