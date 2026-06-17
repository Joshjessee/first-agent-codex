import subprocess
from pathlib import Path
from unittest import mock
import unittest

from news_agent.config import AgentConfig
from news_agent.config import EmailConfig
from news_agent.config import ScheduleConfig
from news_agent.settings_app import create_app


class SettingsAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.personal_dir = Path.cwd() / ".test-personal-topics"
        self.default_personal_config = self.personal_dir / "default.toml"
        self.current_config = AgentConfig(
            topic="artificial intelligence",
            article_count=3,
            email=EmailConfig(
                subject_prefix="Daily Research Digest",
                recipients=("recipient@example.com",),
            ),
            schedule=ScheduleConfig(frequency="daily", time="09:00"),
        )
        app = create_app(personal_config_dir=self.personal_dir)
        app.testing = True
        self.client = app.test_client()

    @mock.patch("news_agent.settings_app.load_config")
    def test_settings_page_loads_current_config(self, load_config_mock: mock.Mock) -> None:
        load_config_mock.return_value = self.current_config

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"artificial intelligence", response.data)
        self.assertIn(b"recipient@example.com", response.data)
        self.assertIn(b"Lookback window", response.data)
        self.assertIn(str(self.default_personal_config).encode("utf-8"), response.data)

    @mock.patch("news_agent.settings_app.Path.exists", return_value=True)
    @mock.patch("news_agent.settings_app.load_config")
    def test_settings_page_can_open_selected_personal_config(
        self,
        load_config_mock: mock.Mock,
        exists_mock: mock.Mock,
    ) -> None:
        load_config_mock.return_value = self.current_config

        response = self.client.get("/?config=briefing.toml")

        expected_path = self.personal_dir / "briefing.toml"
        self.assertEqual(response.status_code, 200)
        load_config_mock.assert_called_once_with(expected_path)
        self.assertIn(str(expected_path).encode("utf-8"), response.data)

    @mock.patch("news_agent.settings_app.write_config")
    @mock.patch("news_agent.settings_app.load_config")
    def test_save_writes_updated_settings(
        self,
        load_config_mock: mock.Mock,
        write_config_mock: mock.Mock,
    ) -> None:
        load_config_mock.return_value = self.current_config

        response = self.client.post(
            "/save",
            data={
                "topic": "climate technology",
                "article_count": "4",
                "lookback_hours": "48",
                "subject_prefix": "Climate Digest",
                "recipients": "a@example.com, b@example.com",
                "frequency": "weekdays",
                "time": "08:15",
                "task_name": "Daily Research Agent",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(write_config_mock.call_args.args[0], self.default_personal_config)
        config = write_config_mock.call_args.args[1]
        self.assertEqual(config.topic, "climate technology")
        self.assertEqual(config.article_count, 4)
        self.assertEqual(config.lookback_hours, 48)
        self.assertEqual(config.email.recipients, ("a@example.com", "b@example.com"))
        self.assertEqual(config.schedule.frequency, "weekdays")
        self.assertEqual(config.schedule.time, "08:15")

    @mock.patch("news_agent.settings_app.write_config")
    @mock.patch("news_agent.settings_app.load_config")
    def test_save_as_new_config_writes_personal_file(
        self,
        load_config_mock: mock.Mock,
        write_config_mock: mock.Mock,
    ) -> None:
        load_config_mock.return_value = self.current_config

        response = self.client.post(
            "/save",
            data={
                "selected_config": "default.toml",
                "new_config_name": "My AI Digest!",
                "topic": "AI",
                "article_count": "3",
                "subject_prefix": "Daily Research Digest",
                "recipients": "me@example.com",
                "frequency": "daily",
                "time": "09:00",
                "task_name": "Daily Research Agent",
            },
        )

        expected_path = self.personal_dir / "my-ai-digest.toml"
        self.assertEqual(response.status_code, 200)
        self.assertEqual(write_config_mock.call_args.args[0], expected_path)
        self.assertIn(str(expected_path).encode("utf-8"), response.data)

    @mock.patch("news_agent.settings_app.subprocess.run")
    @mock.patch("news_agent.settings_app.write_config")
    @mock.patch("news_agent.settings_app.load_config")
    def test_apply_schedule_runs_powershell_script(
        self,
        load_config_mock: mock.Mock,
        write_config_mock: mock.Mock,
        run: mock.Mock,
    ) -> None:
        load_config_mock.return_value = self.current_config
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        response = self.client.post(
            "/apply-schedule",
            data={
                "topic": "AI",
                "article_count": "3",
                "subject_prefix": "Daily Research Digest",
                "recipients": "recipient@example.com",
                "frequency": "daily",
                "time": "09:00",
                "task_name": "Daily Research Agent",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(write_config_mock.call_args.args[0], self.default_personal_config)
        args = run.call_args.args[0]
        self.assertIn("-File", args)
        self.assertIn("-TopicConfigPath", args)
        self.assertEqual(args[args.index("-TopicConfigPath") + 1], str(self.default_personal_config))
        self.assertIn("-At", args)
        self.assertIn("09:00", args)
        self.assertIn("-Frequency", args)
        self.assertIn("Daily", args)

    @mock.patch("news_agent.settings_app.write_config")
    @mock.patch("news_agent.settings_app.load_config")
    def test_invalid_settings_return_validation_error(
        self,
        load_config_mock: mock.Mock,
        write_config_mock: mock.Mock,
    ) -> None:
        load_config_mock.return_value = self.current_config
        cases = [
            {"topic": "", "article_count": "3", "recipients": "recipient@example.com", "time": "09:00"},
            {"topic": "AI", "article_count": "0", "recipients": "recipient@example.com", "time": "09:00"},
            {"topic": "AI", "article_count": "3", "recipients": "not-an-email", "time": "09:00"},
            {"topic": "AI", "article_count": "3", "recipients": "recipient@example.com", "time": "25:99"},
            {"topic": "AI", "article_count": "3", "lookback_hours": "0", "recipients": "recipient@example.com", "time": "09:00"},
        ]
        for case in cases:
            with self.subTest(case=case):
                data = {
                    "subject_prefix": "Daily Research Digest",
                    "frequency": "daily",
                    "task_name": "Daily Research Agent",
                    **case,
                }
                response = self.client.post("/save", data=data)

                self.assertEqual(response.status_code, 400)
        write_config_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
