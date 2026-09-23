from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest import mock
import unittest

from news_agent.collector import ArticleCandidate
from news_agent.config import AgentConfig
from news_agent.openai_ranker import build_digest


class _FakeResponses:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.outputs.pop(0))


class _FakeClient:
    def __init__(self, outputs: list[str]) -> None:
        self.responses = _FakeResponses(outputs)


def _candidates() -> list[ArticleCandidate]:
    return [
        ArticleCandidate(
            index=index,
            title=f"Headline {index}",
            source="Example News",
            url=f"https://example.com/{index}",
            published_at=datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc),
            summary="Snippet",
        )
        for index in (1, 2, 3)
    ]


def _answer(indexes: list[object], overview: str = "A busy day.") -> str:
    return json.dumps(
        {
            "overview": overview,
            "articles": [
                {"index": index, "summary": f"Summary {index}", "why_it_matters": f"Reason {index}"}
                for index in indexes
            ],
        }
    )


class OpenAIRankerTests(unittest.TestCase):
    def test_build_digest_uses_structured_outputs_and_keeps_model_order(self) -> None:
        client = _FakeClient([_answer([3, 1])])

        digest = build_digest(AgentConfig(topic="AI", article_count=2), _candidates(), client=client)

        self.assertEqual([article.title for article in digest.articles], ["Headline 3", "Headline 1"])
        self.assertEqual(digest.articles[0].summary, "Summary 3")
        self.assertEqual(digest.overview, "A busy day.")
        call = client.responses.calls[0]
        self.assertEqual(call["text"]["format"]["type"], "json_schema")
        self.assertTrue(call["text"]["format"]["strict"])
        self.assertIn("untrusted", call["instructions"])
        self.assertIn("Number of articles to select: 2", call["input"])

    def test_build_digest_retries_once_after_an_unusable_answer(self) -> None:
        client = _FakeClient(["not json", _answer([2])])

        with self.assertLogs("news_agent.openai_ranker", level="WARNING"):
            digest = build_digest(AgentConfig(topic="AI", article_count=1), _candidates(), client=client)

        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(digest.articles[0].title, "Headline 2")

    def test_build_digest_gives_up_after_repeated_invalid_indexes(self) -> None:
        client = _FakeClient([_answer([99, "abc"]), _answer([42])])

        with (
            self.assertLogs("news_agent.openai_ranker", level="WARNING"),
            self.assertRaisesRegex(RuntimeError, "did not select enough"),
        ):
            build_digest(AgentConfig(topic="AI", article_count=1), _candidates(), client=client)

    def test_build_digest_ignores_duplicate_indexes(self) -> None:
        client = _FakeClient([_answer([1, 1, 2])])

        digest = build_digest(AgentConfig(topic="AI", article_count=2), _candidates(), client=client)

        self.assertEqual([article.title for article in digest.articles], ["Headline 1", "Headline 2"])

    def test_build_digest_uses_configured_timezone(self) -> None:
        client = _FakeClient([_answer([1])])

        digest = build_digest(
            AgentConfig(topic="AI", article_count=1, timezone="America/Phoenix"),
            _candidates(),
            client=client,
        )

        self.assertEqual(digest.generated_at.utcoffset().total_seconds(), -7 * 3600)

    @mock.patch.dict("os.environ", {}, clear=True)
    def test_build_digest_requires_api_key_without_client(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            build_digest(AgentConfig(topic="AI"), _candidates())

    def test_build_digest_rejects_empty_candidates(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "No recent article candidates"):
            build_digest(AgentConfig(topic="AI"), [], client=_FakeClient([]))


if __name__ == "__main__":
    unittest.main()
