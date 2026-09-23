# Daily Research Agent

A small, shareable agent harness that finds recent articles for a topic, asks OpenAI to pick the 3 most important ones, and emails a daily digest.

The first version is intentionally simple: people customize the topic, while the collection, ranking, summarizing, and delivery code stay reusable.

## What It Does

1. Reads a topic and source settings from the selected topic config.
2. Pulls recent article candidates from Google News RSS, searching across your whole lookback window.
3. Optionally reads any extra RSS/Atom feeds you list (blogs, publications, arXiv, and so on).
4. Optionally reads recent Gmail newsletters from selected senders or labels and extracts article links.
5. Filters out old articles, articles matching your exclude keywords, and articles it already emailed you recently.
6. Dedupes the combined candidate pool, mixing sources fairly so a big source cannot crowd out a small one.
7. Uses the OpenAI Responses API with Structured Outputs to rank and summarize the biggest articles, plus a short "big picture" overview.
8. Sends the digest by email and remembers what it sent.

If one source breaks (for example, a Gmail login fails), the agent logs a warning and carries on with the others.

OpenAI's current docs recommend the Responses API for new text generation projects, and the official SDK reads `OPENAI_API_KEY` from your environment.

References:
- [Responses API reference](https://platform.openai.com/docs/api-reference/responses?lang=python)
- [Text generation guide](https://platform.openai.com/docs/guides/text-generation?lang=python)
- [Python library setup](https://platform.openai.com/docs/libraries/python)

## Setup

Create a virtual environment and install the project:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` with your OpenAI API key and email delivery settings.

For Gmail, use an app password, not your normal Google password.

The optional Gmail newsletter source also uses IMAP. It reads `GMAIL_USERNAME`
and `GMAIL_PASSWORD` from `.env`, or falls back to `SMTP_USERNAME` and
`SMTP_PASSWORD` when those are not set.

## Configure It In The Browser

Start the local settings page:

```powershell
daily-research-agent-settings
```

Open `http://127.0.0.1:8765`, then set the topic, number of articles, article lookback window, extra RSS feeds, exclude keywords, whether to avoid repeats, recipients, subject prefix, delivery time, timezone, and frequency. Use the personal config picker to open an existing personal config, or enter a name in **Save as new personal config** before saving.

The settings page writes personal topic configs under `config/personal_topics/`. On first use, it loads `config/topics/ai.toml` only as a starter template, then saves to `config/personal_topics/default.toml` or the new personal file you name. API keys and SMTP passwords stay in `.env`.

## Configure It By File

Advanced users can also edit a personal config directly after creating it in the settings page, for example `config/personal_topics/default.toml`:

```toml
topic = "artificial intelligence"
article_count = 3
lookback_hours = 30
timezone = "America/Phoenix" # optional; blank uses the computer's local time
max_candidates = 25          # how many articles OpenAI chooses from (5-100)
exclude_keywords = ["crypto", "rumor"]

[email]
subject_prefix = "Daily Research Digest"
recipients = ["recipient@example.com"]

[schedule]
frequency = "daily"
time = "09:00"

[sources.google_news]
enabled = true

[sources.gmail]
enabled = false
mode = "senders" # senders, labels, or senders_and_labels
senders = ["newsletter@example.com"]
labels = []
max_messages = 25
max_links_per_message = 6

[sources.rss]
enabled = true
feeds = ["https://example.com/feed.xml"]
max_items_per_feed = 10

[history]
enabled = true # skip stories that were already emailed
days = 7       # ...within this many days
```

Gmail source modes:

- `senders`: reads recent messages from the configured senders in `INBOX`.
- `labels`: reads recent messages from the configured Gmail labels.
- `senders_and_labels`: reads configured labels, filtered to the configured senders.

Newsletter links are normalized into the same candidate shape as Google News
RSS entries, then deduped by headline and canonical URL before ranking.

To share this agent with someone else, they can copy the project and use the settings page to create their own personal topic config, recipients, and schedule without changing the shared sample.

## Run It Manually

Preview the digest without sending email:

```powershell
python -m news_agent --config config/personal_topics/default.toml --dry-run
```

Send the email:

```powershell
python -m news_agent --config config/personal_topics/default.toml
```

Other helpful options:

| Option | What it does |
| --- | --- |
| `--show-candidates` | Lists every article the agent found, then stops before calling OpenAI. Great for checking your sources and filters for free. |
| `--preview-html digest.preview.html` | Saves the email as an HTML file you can open in a browser. Pair it with `--dry-run`. |
| `--no-history` | Ignores the "already sent" memory for this run and does not record anything. |
| `--history-file PATH` | Stores the memory somewhere other than `.agent-state/<config name>-history.json`. |
| `-v`, `--verbose` | Prints detailed progress logs. |

## Avoiding Repeat Stories

After each successful send, the agent records the emailed articles in
`.agent-state/<config name>-history.json`. On later runs it skips any candidate
with the same link or headline that was sent within `[history].days` days, so a
big story does not show up in your inbox three days in a row. Dry runs never
record anything. Delete the file (or set `[history].enabled = false`) to reset it.

## Email Server Notes

Port `465` uses SSL and port `587` uses STARTTLS automatically. For other ports,
set `SMTP_SECURITY=ssl` or `SMTP_SECURITY=starttls` in `.env`. The agent
verifies the mail server's certificate before sending your password.

## Schedule It From PowerShell

The settings page is the friendliest way to apply the schedule. Advanced users can run the scheduler script directly:

```powershell
.\scripts\setup_windows_task.ps1 -TopicConfigPath "config\personal_topics\default.toml" -TaskName "Daily Research Agent" -At "09:00" -Frequency Daily
```

That command schedules your personal config to run every day at 9:00 AM using the local virtual environment. Use `-Frequency Weekdays` for Monday through Friday. If you omit `-TopicConfigPath`, the scheduler defaults to `config\personal_topics\default.toml`.

## Schedule It With GitHub Actions

The repository includes `.github/workflows/daily-digest.yml`, which runs on GitHub's hosted runner every day at 9:00 AM America/Phoenix time. It can also be started manually from the Actions tab.

Add these repository secrets in GitHub under **Settings > Secrets and variables > Actions**:

- `TOPIC_CONFIG_TOML`: the full TOML contents of your personal topic config.
- `OPENAI_API_KEY`: your OpenAI API key.
- `SMTP_HOST`: your SMTP server.
- `SMTP_USERNAME`: your SMTP username.
- `SMTP_PASSWORD`: your SMTP password or app password.

Optional secrets:

- `SMTP_PORT`: defaults to `465` when omitted.
- `SMTP_SECURITY`: `ssl` or `starttls`; only needed for unusual ports.
- `OPENAI_MODEL`: defaults to `gpt-5` when omitted.
- `EMAIL_FROM`: defaults to `SMTP_USERNAME` when omitted.
- `EMAIL_TO`: fallback recipients when the TOML config does not include `[email].recipients`.
- `GMAIL_USERNAME`, `GMAIL_PASSWORD`, `GMAIL_IMAP_HOST`, `GMAIL_IMAP_PORT`: only needed when the Gmail source is enabled.

The workflow writes `TOPIC_CONFIG_TOML` to `config/personal_topics/github.toml` at runtime. If that secret is not set, it will use a committed `config/personal_topics/default.toml`; otherwise it fails. It does not fall back to the shared sample config.

The workflow keeps the "already sent" memory between runs with the GitHub Actions
cache, so repeat stories are skipped on GitHub too. GitHub removes caches that go
unused for 7 days. If the workflow pauses for longer than that, the memory simply
starts fresh.

## Shareable Agent Shape

The harness is split by responsibility:

- `collector.py`: finds candidate articles from Google News RSS, extra RSS feeds, and optional Gmail newsletters.
- `history.py`: remembers which articles were already emailed.
- `openai_ranker.py`: asks OpenAI to select and summarize the top articles.
- `digest.py`: formats the digest as plain text and HTML email.
- `emailer.py`: delivers the digest.
- `config.py`: loads user-editable topic config.
- `settings_app.py`: the local browser settings page.
- `cli.py`: wires the agent together.

That structure makes it easy to later add more output channels, more source types, or a small UI without rewriting the core agent.

## Run The Tests

Install the test tools once, then run the test suite:

```powershell
pip install -e ".[dev]"
python -m pytest
```

GitHub also runs these tests automatically on every push and pull request (see `.github/workflows/tests.yml`).
