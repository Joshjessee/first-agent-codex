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


if __name__ == "__main__":
    unittest.main()
