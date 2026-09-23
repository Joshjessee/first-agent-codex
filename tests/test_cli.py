from datetime import datetime, timezone
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
from unittest import mock
import unittest

from news_agent import cli
from news_agent.collector import ArticleCandidate
from news_agent.digest import Digest
from news_agent.digest import DigestArticle


CONFIG_TOML = """
topic = "AI"

[email]
recipients = ["reader@example.com"]
"""


def _candidate() -> ArticleCandidate:
    return ArticleCandidate(
        index=1,
        title="Big AI news",
        source="Example News",
        url="https://example.com/big",
        published_at=datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc),
        summary="",
    )


def _digest() -> Digest:
    return Digest(
        topic="AI",
        generated_at=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
        articles=[
            DigestArticle(
                title="Big AI news",
                source="Example News",
                url="https://example.com/big",
                summary="Summary.",
                why_it_matters="Reason.",
            )
        ],
        overview="A busy day.",
    )


@mock.patch("news_agent.cli.load_dotenv")
@mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True)
class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(CONFIG_TOML, encoding="utf-8")
        self.history_path = self.root / "history.json"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _main(self, *args: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output), self.assertLogs("news_agent", level="INFO"):
            cli.main(["--config", str(self.config_path), "--history-file", str(self.history_path), *args])
        return output.getvalue()

    @mock.patch("news_agent.cli.send_digest_email")
    @mock.patch("news_agent.cli.build_digest", return_value=_digest())
    @mock.patch("news_agent.cli.collect_candidates", return_value=[_candidate()])
    def test_dry_run_prints_digest_writes_preview_and_does_not_send(
        self, collect: mock.Mock, build: mock.Mock, send: mock.Mock, load_dotenv: mock.Mock
    ) -> None:
        preview_path = self.root / "preview" / "digest.html"

        output = self._main("--dry-run", "--preview-html", str(preview_path))

        self.assertIn("The big picture: A busy day.", output)
        self.assertIn("Big AI news", preview_path.read_text(encoding="utf-8"))
        send.assert_not_called()
        self.assertFalse(self.history_path.exists())

    @mock.patch("news_agent.cli.send_digest_email")
    @mock.patch("news_agent.cli.build_digest", return_value=_digest())
    @mock.patch("news_agent.cli.collect_candidates", return_value=[_candidate()])
    def test_sending_records_history_that_the_next_run_skips(
        self, collect: mock.Mock, build: mock.Mock, send: mock.Mock, load_dotenv: mock.Mock
    ) -> None:
        self._main()
        send.assert_called_once()
        self.assertTrue(self.history_path.exists())

        self._main()
        skip = collect.call_args.kwargs["skip"]
        self.assertTrue(skip(_candidate()))

    @mock.patch("news_agent.cli.build_digest")
    @mock.patch("news_agent.cli.collect_candidates", return_value=[_candidate()])
    def test_show_candidates_stops_before_openai(
        self, collect: mock.Mock, build: mock.Mock, load_dotenv: mock.Mock
    ) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            output = self._main("--show-candidates", "--no-history")

        self.assertIn("1. Big AI news", output)
        self.assertIn("https://example.com/big", output)
        build.assert_not_called()
        self.assertIsNone(collect.call_args.kwargs["skip"])

    def test_missing_config_exits_with_friendly_message(self, load_dotenv: mock.Mock) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli.main(["--config", str(self.root / "missing.toml")])

        self.assertIn("config file not found", str(raised.exception.code))
        self.assertIn("daily-research-agent-settings", str(raised.exception.code))

    def test_invalid_toml_exits_with_friendly_message(self, load_dotenv: mock.Mock) -> None:
        self.config_path.write_text("topic = ", encoding="utf-8")

        with self.assertRaises(SystemExit) as raised:
            cli.main(["--config", str(self.config_path)])

        self.assertIn("not valid TOML", str(raised.exception.code))


if __name__ == "__main__":
    unittest.main()
