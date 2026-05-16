import shutil
import subprocess
from pathlib import Path
import unittest


class SchedulerScriptTests(unittest.TestCase):
    def test_setup_windows_task_defaults_to_personal_config(self) -> None:
        script = Path("scripts/setup_windows_task.ps1").read_text(encoding="utf-8")

        self.assertIn('[string]$TopicConfigPath = "config\\personal_topics\\default.toml"', script)
        self.assertNotIn('[string]$TopicConfigPath = "config\\topics\\ai.toml"', script)

    def test_setup_windows_task_script_parses(self) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is not available")

        command = """
$tokens = $null
$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile('scripts/setup_windows_task.ps1', [ref]$tokens, [ref]$errors)
if ($errors) {
    $errors | Format-List *
    exit 1
}
"""
        completed = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
