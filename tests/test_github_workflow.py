from pathlib import Path
import unittest


class GitHubWorkflowTests(unittest.TestCase):
    def test_daily_digest_workflow_uses_personal_config_secret(self) -> None:
        workflow = Path(".github/workflows/daily-digest.yml").read_text(encoding="utf-8")

        self.assertIn('cron: "0 16 * * *"', workflow)
        self.assertIn("TOPIC_CONFIG_TOML", workflow)
        self.assertIn("config/personal_topics/github.toml", workflow)
        self.assertIn("config/personal_topics/default.toml", workflow)
        self.assertIn("will not fall back to config/topics/ai.toml", workflow)
        self.assertIn('python -m news_agent --config "$TOPIC_CONFIG"', workflow)

    def test_daily_digest_workflow_persists_sent_history(self) -> None:
        workflow = Path(".github/workflows/daily-digest.yml").read_text(encoding="utf-8")

        self.assertIn("actions/cache@v5", workflow)
        self.assertIn("path: .agent-state", workflow)
        self.assertIn("restore-keys", workflow)

    def test_tests_workflow_runs_pytest(self) -> None:
        workflow = Path(".github/workflows/tests.yml").read_text(encoding="utf-8")

        self.assertIn('pip install -e ".[dev]"', workflow)
        self.assertIn("python -m pytest", workflow)


if __name__ == "__main__":
    unittest.main()
