from datetime import datetime, timezone
import unittest

from news_agent.digest import Digest, DigestArticle


class DigestTests(unittest.TestCase):
    def test_digest_text_contains_expected_article_fields(self) -> None:
        digest = Digest(
            topic="AI",
            generated_at=datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc),
            articles=[
                DigestArticle(
                    title="Example headline",
                    source="Example source",
                    url="https://example.com/article",
                    summary="A concise summary.",
                    why_it_matters="A concise reason.",
                )
            ],
        )

        text = digest.to_text()

        self.assertIn("Daily Research Digest: AI", text)
        self.assertIn("Example headline", text)
        self.assertIn("Why it matters: A concise reason.", text)

    def test_digest_html_uses_styled_template_and_escapes_content(self) -> None:
        digest = Digest(
            topic="AI <research>",
            generated_at=datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc),
            articles=[
                DigestArticle(
                    title="Example <headline>",
                    source="Example source",
                    url="https://example.com/article?a=1&b=2",
                    summary="A concise <summary>.",
                    why_it_matters="A concise reason.",
                )
            ],
        )

        html = digest.to_html()

        self.assertIn("Daily Research Digest", html)
        self.assertIn("Read article", html)
        self.assertIn("background: #102a43", html)
        self.assertIn("AI &lt;research&gt;", html)
        self.assertIn("Example &lt;headline&gt;", html)
        self.assertIn("https://example.com/article?a=1&amp;b=2", html)


if __name__ == "__main__":
    unittest.main()
