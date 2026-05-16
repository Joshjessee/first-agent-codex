from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
import re
import subprocess

from flask import Flask
from flask import request
from flask import render_template_string

from news_agent.config import AgentConfig
from news_agent.config import EmailConfig
from news_agent.config import ScheduleConfig
from news_agent.config import load_config
from news_agent.config import write_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "topics" / "ai.toml"
PERSONAL_CONFIG_DIR = PROJECT_ROOT / "config" / "personal_topics"
DEFAULT_CONFIG_PATH = PERSONAL_CONFIG_DIR / "default.toml"
DEFAULT_TASK_NAME = "Daily Research Agent"


def create_app(
    config_path: Path | None = None,
    *,
    personal_config_dir: Path = PERSONAL_CONFIG_DIR,
) -> Flask:
    app = Flask(__name__)
    app.config["PERSONAL_CONFIG_DIR"] = Path(personal_config_dir)
    app.config["TOPIC_CONFIG_PATH"] = Path(config_path) if config_path is not None else _default_personal_config_path(app)

    @app.get("/")
    def index() -> str:
        config_path = _config_path_from_name(request.args.get("config"), app)
        app.config["TOPIC_CONFIG_PATH"] = config_path
        config = _load_config_or_sample(config_path)
        return _render_settings(config, config_path=config_path, saved=False, scheduled=False, errors=[], app=app)

    @app.post("/save")
    def save() -> tuple[str, int] | str:
        config_path = _config_path_from_form(request.form, app)
        result = _config_from_form(_load_config_or_sample(config_path), request.form)
        if result.errors:
            return _render_settings(result.config, config_path=config_path, saved=False, scheduled=False, errors=result.errors, app=app), 400

        write_config(config_path, result.config)
        app.config["TOPIC_CONFIG_PATH"] = config_path
        return _render_settings(result.config, config_path=config_path, saved=True, scheduled=False, errors=[], app=app)

    @app.post("/apply-schedule")
    def apply_schedule() -> tuple[str, int] | str:
        config_path = _config_path_from_form(request.form, app)
        result = _config_from_form(_load_config_or_sample(config_path), request.form)
        if result.errors:
            return _render_settings(result.config, config_path=config_path, saved=False, scheduled=False, errors=result.errors, app=app), 400

        write_config(config_path, result.config)
        app.config["TOPIC_CONFIG_PATH"] = config_path
        script_path = PROJECT_ROOT / "scripts" / "setup_windows_task.ps1"
        completed = subprocess.run(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-TopicConfigPath",
                str(config_path),
                "-TaskName",
                request.form.get("task_name", DEFAULT_TASK_NAME).strip() or DEFAULT_TASK_NAME,
                "-At",
                result.config.schedule.time,
                "-Frequency",
                result.config.schedule.frequency.capitalize(),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "Unable to update scheduled task."
            return _render_settings(result.config, config_path=config_path, saved=True, scheduled=False, errors=[message], app=app), 500

        return _render_settings(result.config, config_path=config_path, saved=True, scheduled=True, errors=[], app=app)

    return app


class _FormResult:
    def __init__(self, config: AgentConfig, errors: list[str]) -> None:
        self.config = config
        self.errors = errors


def _config_from_form(current: AgentConfig, form: object) -> _FormResult:
    topic = str(form.get("topic", "")).strip()
    subject_prefix = str(form.get("subject_prefix", "")).strip() or "Daily Research Digest"
    recipients = _parse_recipients(str(form.get("recipients", "")))
    frequency = str(form.get("frequency", "daily")).strip().lower()
    time = _normalize_time(str(form.get("time", "09:00")))

    errors = []
    if not topic:
        errors.append("Topic is required.")

    try:
        article_count = int(str(form.get("article_count", "3")).strip())
    except ValueError:
        article_count = current.article_count
        errors.append("Number of articles must be a whole number.")

    if not 1 <= article_count <= 10:
        errors.append("Number of articles must be between 1 and 10.")

    invalid_recipients = [recipient for recipient in recipients if not _looks_like_email(recipient)]
    if not recipients:
        errors.append("At least one recipient email is required.")
    elif invalid_recipients:
        errors.append(f"Invalid recipient email: {invalid_recipients[0]}")

    if frequency not in {"daily", "weekdays"}:
        errors.append("Frequency must be daily or weekdays.")
        frequency = current.schedule.frequency

    if time is None:
        errors.append("Delivery time must be a valid time like 09:00 or 9:00 AM.")
        time = current.schedule.time

    config = replace(
        current,
        topic=topic,
        article_count=article_count,
        email=EmailConfig(subject_prefix=subject_prefix, recipients=recipients),
        schedule=ScheduleConfig(frequency=frequency, time=time),
    )
    return _FormResult(config, errors)


def _parse_recipients(value: str) -> tuple[str, ...]:
    parts = value.replace(";", ",").replace("\n", ",").split(",")
    return tuple(part.strip() for part in parts if part.strip())


def _looks_like_email(value: str) -> bool:
    _, address = parseaddr(value)
    return address == value and "@" in address and "." in address.rsplit("@", 1)[-1]


def _normalize_time(value: str) -> str | None:
    text = value.strip()
    for format_text in ("%H:%M", "%I:%M %p", "%I:%M%p", "%H:%M:%S"):
        try:
            return datetime.strptime(text, format_text).strftime("%H:%M")
        except ValueError:
            continue
    return None


def _default_personal_config_path(app: Flask) -> Path:
    return Path(app.config["PERSONAL_CONFIG_DIR"]) / "default.toml"


def _load_config_or_sample(path: Path) -> AgentConfig:
    if path.exists():
        return load_config(path)
    return load_config(SAMPLE_CONFIG_PATH)


def _config_path_from_form(form: object, app: Flask) -> Path:
    new_name = str(form.get("new_config_name", "")).strip()
    if new_name:
        return _personal_config_path(_slugify_config_name(new_name), app)
    return _config_path_from_name(str(form.get("selected_config", "")).strip(), app)


def _config_path_from_name(name: str | None, app: Flask) -> Path:
    if not name:
        return Path(app.config["TOPIC_CONFIG_PATH"])
    return _personal_config_path(name, app)


def _personal_config_path(name: str, app: Flask) -> Path:
    personal_dir = Path(app.config["PERSONAL_CONFIG_DIR"]).resolve()
    candidate = (personal_dir / Path(name).name).with_suffix(".toml").resolve()
    if candidate.parent != personal_dir:
        return _default_personal_config_path(app)
    return candidate


def _slugify_config_name(value: str) -> str:
    stem = Path(value).stem
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_").lower()
    return f"{slug or 'default'}.toml"


def _personal_config_choices(app: Flask, selected_path: Path) -> list[Path]:
    personal_dir = Path(app.config["PERSONAL_CONFIG_DIR"])
    choices = sorted(personal_dir.glob("*.toml")) if personal_dir.exists() else []
    default_path = _default_personal_config_path(app)
    if default_path not in choices:
        choices.insert(0, default_path)
    if selected_path.parent == personal_dir and selected_path not in choices:
        choices.append(selected_path)
    return choices


def _render_settings(
    config: AgentConfig,
    *,
    config_path: Path,
    saved: bool,
    scheduled: bool,
    errors: list[str],
    app: Flask,
) -> str:
    return render_template_string(
        SETTINGS_TEMPLATE,
        config=config,
        config_path=config_path,
        selected_config=config_path.name,
        config_choices=_personal_config_choices(app, config_path),
        saved=saved,
        scheduled=scheduled,
        errors=errors,
        recipients=", ".join(config.email.recipients),
        task_name=DEFAULT_TASK_NAME,
    )


def main() -> None:
    app = create_app()
    app.run(host="127.0.0.1", port=8765)


SETTINGS_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Daily Research Agent Settings</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f6f7f9;
        --panel: #ffffff;
        --text: #1f2937;
        --muted: #5f6b7a;
        --border: #d8dee8;
        --accent: #0f766e;
        --accent-dark: #115e59;
        --danger: #b42318;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: var(--bg);
        color: var(--text);
        font-family: Arial, sans-serif;
        line-height: 1.5;
      }
      main {
        width: min(920px, calc(100% - 32px));
        margin: 32px auto;
      }
      header {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 20px;
        margin-bottom: 22px;
      }
      h1 {
        margin: 0 0 4px;
        font-size: 28px;
        line-height: 1.2;
      }
      .subtle {
        margin: 0;
        color: var(--muted);
        font-size: 14px;
      }
      .status {
        border: 1px solid var(--border);
        border-left: 4px solid var(--accent);
        background: #eefcf8;
        border-radius: 6px;
        padding: 12px 14px;
        margin-bottom: 16px;
      }
      .errors {
        border-left-color: var(--danger);
        background: #fff3f0;
      }
      form {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 24px;
      }
      .config-picker {
        margin-bottom: 16px;
      }
      .config-picker .grid {
        align-items: end;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 18px;
      }
      label {
        display: block;
        margin-bottom: 7px;
        font-weight: 700;
        font-size: 14px;
      }
      input, select, textarea {
        width: 100%;
        min-height: 42px;
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 9px 11px;
        color: var(--text);
        font: inherit;
        background: #ffffff;
      }
      textarea {
        min-height: 92px;
        resize: vertical;
      }
      .full { grid-column: 1 / -1; }
      .actions {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        margin-top: 22px;
      }
      button {
        border: 0;
        border-radius: 6px;
        padding: 10px 14px;
        font: inherit;
        font-weight: 700;
        cursor: pointer;
      }
      .primary {
        background: var(--accent);
        color: #ffffff;
      }
      .primary:hover { background: var(--accent-dark); }
      .secondary {
        border: 1px solid var(--border);
        background: #ffffff;
        color: var(--text);
      }
      .path {
        margin-top: 10px;
        color: var(--muted);
        font-size: 13px;
        overflow-wrap: anywhere;
      }
      @media (max-width: 700px) {
        header { display: block; }
        .grid { grid-template-columns: 1fr; }
        main { margin-top: 20px; }
      }
    </style>
  </head>
  <body>
    <main>
      <header>
        <div>
          <h1>Daily Research Agent Settings</h1>
          <p class="subtle">Local settings for the scheduled email digest.</p>
        </div>
      </header>

      {% if errors %}
        <div class="status errors">
          {% for error in errors %}
            <div>{{ error }}</div>
          {% endfor %}
        </div>
      {% elif scheduled %}
        <div class="status">Settings saved and the Windows scheduled task was updated.</div>
      {% elif saved %}
        <div class="status">Settings saved.</div>
      {% endif %}

      <form class="config-picker" method="get">
        <div class="grid">
          <div>
            <label for="config">Personal config</label>
            <select id="config" name="config">
              {% for choice in config_choices %}
                <option value="{{ choice.name }}" {% if choice.name == selected_config %}selected{% endif %}>{{ choice.stem }}</option>
              {% endfor %}
            </select>
          </div>
          <div>
            <button class="secondary" type="submit">Open config</button>
          </div>
        </div>
        <div class="path">Current file: {{ config_path }}</div>
      </form>

      <form method="post">
        <input type="hidden" name="selected_config" value="{{ selected_config }}">
        <div class="grid">
          <div class="full">
            <label for="new_config_name">Save as new personal config</label>
            <input id="new_config_name" name="new_config_name" placeholder="Leave blank to update {{ config_path.stem }}">
          </div>

          <div class="full">
            <label for="topic">Topic</label>
            <input id="topic" name="topic" value="{{ config.topic }}" required>
          </div>

          <div>
            <label for="article_count">Articles per email</label>
            <input id="article_count" name="article_count" type="number" min="1" max="10" value="{{ config.article_count }}" required>
          </div>

          <div>
            <label for="subject_prefix">Subject prefix</label>
            <input id="subject_prefix" name="subject_prefix" value="{{ config.email.subject_prefix }}" required>
          </div>

          <div class="full">
            <label for="recipients">Recipients</label>
            <textarea id="recipients" name="recipients" required>{{ recipients }}</textarea>
          </div>

          <div>
            <label for="frequency">Frequency</label>
            <select id="frequency" name="frequency">
              <option value="daily" {% if config.schedule.frequency == "daily" %}selected{% endif %}>Daily</option>
              <option value="weekdays" {% if config.schedule.frequency == "weekdays" %}selected{% endif %}>Weekdays</option>
            </select>
          </div>

          <div>
            <label for="time">Delivery time</label>
            <input id="time" name="time" type="time" value="{{ config.schedule.time }}" required>
          </div>

          <div class="full">
            <label for="task_name">Windows task name</label>
            <input id="task_name" name="task_name" value="{{ task_name }}" required>
          </div>
        </div>

        <div class="actions">
          <button class="primary" type="submit" formaction="/save">Save settings</button>
          <button class="secondary" type="submit" formaction="/apply-schedule">Save and apply schedule</button>
        </div>
      </form>
    </main>
  </body>
</html>
"""
