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

    try:
        lookback_hours = int(str(form.get("lookback_hours", str(current.lookback_hours))).strip())
    except ValueError:
        lookback_hours = current.lookback_hours
        errors.append("Lookback window must be a whole number of hours.")

    if not 1 <= lookback_hours <= 168:
        errors.append("Lookback window must be between 1 and 168 hours.")

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
        lookback_hours=lookback_hours,
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
        --bg: #eef5ff;
        --panel: rgba(255, 255, 255, 0.94);
        --text: #172033;
        --muted: #64748b;
        --border: #d7e3f4;
        --accent: #0f766e;
        --accent-dark: #115e59;
        --accent-soft: #ccfbf1;
        --danger: #b42318;
        --shadow: 0 24px 60px rgba(15, 23, 42, 0.12);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(circle at top left, rgba(20, 184, 166, 0.24), transparent 34rem),
          linear-gradient(135deg, #f8fbff 0%, var(--bg) 52%, #e7f8f5 100%);
        color: var(--text);
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        line-height: 1.5;
      }
      main {
        width: min(1040px, calc(100% - 32px));
        margin: 40px auto;
      }
      header {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 20px;
        margin-bottom: 22px;
      }
      .eyebrow {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 12px;
        border: 1px solid var(--border);
        border-radius: 999px;
        background: rgba(255,255,255,0.72);
        padding: 6px 11px;
        color: var(--accent-dark);
        font-size: 13px;
        font-weight: 800;
      }
      h1 {
        margin: 0 0 8px;
        font-size: clamp(32px, 5vw, 52px);
        line-height: 1.02;
        letter-spacing: -0.05em;
      }
      .subtle {
        margin: 0;
        max-width: 620px;
        color: var(--muted);
        font-size: 16px;
      }
      .summary-card {
        align-self: end;
        min-width: 220px;
        border: 1px solid var(--border);
        border-radius: 18px;
        background: var(--panel);
        box-shadow: var(--shadow);
        padding: 18px;
      }
      .summary-card strong { display: block; font-size: 22px; }
      .summary-card span { color: var(--muted); font-size: 13px; }
      .status {
        border: 1px solid var(--border);
        border-left: 5px solid var(--accent);
        background: #eefcf8;
        border-radius: 14px;
        padding: 13px 16px;
        margin-bottom: 16px;
        box-shadow: 0 12px 26px rgba(15, 118, 110, 0.08);
      }
      .errors {
        border-left-color: var(--danger);
        background: #fff3f0;
      }
      form {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 20px;
        padding: 24px;
        box-shadow: var(--shadow);
        backdrop-filter: blur(12px);
      }
      .config-picker {
        margin-bottom: 18px;
        box-shadow: 0 14px 34px rgba(15, 23, 42, 0.08);
      }
      .config-picker .grid { align-items: end; }
      .section-title {
        grid-column: 1 / -1;
        margin: 6px 0 -4px;
        color: var(--accent-dark);
        font-size: 13px;
        font-weight: 900;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 18px;
      }
      label {
        display: block;
        margin-bottom: 7px;
        font-weight: 800;
        font-size: 14px;
      }
      input, select, textarea {
        width: 100%;
        min-height: 44px;
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 10px 12px;
        color: var(--text);
        font: inherit;
        background: #ffffff;
        transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
      }
      input:focus, select:focus, textarea:focus {
        outline: none;
        border-color: var(--accent);
        box-shadow: 0 0 0 4px rgba(15, 118, 110, 0.14);
      }
      textarea { min-height: 96px; resize: vertical; }
      .hint { margin: 7px 0 0; color: var(--muted); font-size: 12px; }
      .full { grid-column: 1 / -1; }
      .actions {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        margin-top: 24px;
      }
      button {
        border: 0;
        border-radius: 999px;
        padding: 11px 17px;
        font: inherit;
        font-weight: 900;
        cursor: pointer;
        transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
      }
      button:hover { transform: translateY(-1px); }
      .primary {
        background: linear-gradient(135deg, var(--accent), #14b8a6);
        color: #ffffff;
        box-shadow: 0 12px 24px rgba(15, 118, 110, 0.24);
      }
      .primary:hover { background: linear-gradient(135deg, var(--accent-dark), var(--accent)); }
      .secondary {
        border: 1px solid var(--border);
        background: #ffffff;
        color: var(--text);
      }
      .path {
        margin-top: 12px;
        color: var(--muted);
        font-size: 13px;
        overflow-wrap: anywhere;
      }
      @media (max-width: 760px) {
        header { grid-template-columns: 1fr; }
        .summary-card { min-width: 0; }
        .grid { grid-template-columns: 1fr; }
        main { margin-top: 24px; }
      }
    </style>
  </head>
  <body>
    <main>
      <header>
        <div>
          <div class="eyebrow">✦ Local digest control center</div>
          <h1>Daily Research Agent Settings</h1>
          <p class="subtle">Tune the topic, delivery schedule, recipients, and collection window for your scheduled email digest.</p>
        </div>
        <div class="summary-card" aria-label="Current digest summary">
          <strong>{{ config.article_count }} articles</strong>
          <span>from the last {{ config.lookback_hours }} hours</span>
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
          <div><button class="secondary" type="submit">Open config</button></div>
        </div>
        <div class="path">Current file: {{ config_path }}</div>
      </form>

      <form method="post">
        <input type="hidden" name="selected_config" value="{{ selected_config }}">
        <div class="grid">
          <div class="section-title">Profile</div>
          <div class="full">
            <label for="new_config_name">Save as new personal config</label>
            <input id="new_config_name" name="new_config_name" placeholder="Leave blank to update {{ config_path.stem }}">
          </div>
          <div class="full">
            <label for="topic">Topic</label>
            <input id="topic" name="topic" value="{{ config.topic }}" required>
          </div>

          <div class="section-title">Digest content</div>
          <div>
            <label for="article_count">Articles per email</label>
            <input id="article_count" name="article_count" type="number" min="1" max="10" value="{{ config.article_count }}" required>
            <p class="hint">Choose 1–10 ranked stories.</p>
          </div>
          <div>
            <label for="lookback_hours">Lookback window</label>
            <input id="lookback_hours" name="lookback_hours" type="number" min="1" max="168" value="{{ config.lookback_hours }}" required>
            <p class="hint">How many recent hours to scan, up to 7 days.</p>
          </div>

          <div class="section-title">Email delivery</div>
          <div>
            <label for="subject_prefix">Subject prefix</label>
            <input id="subject_prefix" name="subject_prefix" value="{{ config.email.subject_prefix }}" required>
          </div>
          <div>
            <label for="frequency">Frequency</label>
            <select id="frequency" name="frequency">
              <option value="daily" {% if config.schedule.frequency == "daily" %}selected{% endif %}>Daily</option>
              <option value="weekdays" {% if config.schedule.frequency == "weekdays" %}selected{% endif %}>Weekdays</option>
            </select>
          </div>
          <div class="full">
            <label for="recipients">Recipients</label>
            <textarea id="recipients" name="recipients" required>{{ recipients }}</textarea>
            <p class="hint">Separate multiple recipients with commas, semicolons, or new lines.</p>
          </div>
          <div>
            <label for="time">Delivery time</label>
            <input id="time" name="time" type="time" value="{{ config.schedule.time }}" required>
          </div>
          <div>
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
