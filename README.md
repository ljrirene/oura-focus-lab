# Oura Focus Lab

[简体中文](README.zh-CN.md)

A local-first toolkit for downloading your Oura API V2 history, exploring sleep and recovery patterns, and turning the latest night into a concrete daily plan.

The browser dashboard runs on your computer. OAuth tokens, health CSV files, personal profiles, daily item logs, and generated reports are excluded from Git by default.

> This project is an independent community project and is not affiliated with or endorsed by Oura Health Oy. It is for personal exploration, not diagnosis or medical treatment.

## Features

- OAuth 2.0 authorization for Oura API V2
- Paginated historical sync with rate-limit handling
- Raw JSON snapshots and incrementally merged CSV tables
- Automatic rolling sync of recent data with visible status and retry
- Optional AI-generated daily schedule using compact Oura summaries
- Local dashboard for Readiness, sleep, HRV, schedules, and trends
- User-defined medication, supplement, or routine check-offs stored only on the local machine
- Calendar reminder export
- Installable PWA shell for phone access
- Exploratory cognition, sleep-cycle, and longitudinal reports
- No runtime dependencies outside the Python standard library

## Privacy First

The following paths are ignored and must remain private:

```text
.env
.oura/
data/raw/
data/csv/*.csv
data/cognitive_log.csv
data/daily_item_log.json
data/exports/
reports/*.md
config/user.json
```

Before every public push, run:

```bash
git status --short
git check-ignore .env .oura/tokens.json data/csv/sleep.csv
```

See [Privacy and Security](docs/PRIVACY.md) for the threat model and safe sharing checklist.

## Quick Start

Requirements: Python 3.8 or newer and an Oura developer application.

1. Register an OAuth application at [Oura Cloud](https://cloud.ouraring.com/oauth/applications).
2. Set the redirect URI to `http://localhost:8765/callback`.
3. Create local configuration:

```bash
cp .env.example .env
cp config/profile.example.json config/user.json
```

4. Add your client ID and client secret to `.env`.
5. Authorize the app:

```bash
python3 scripts/oura_sync.py auth
```

6. Download history:

```bash
python3 scripts/oura_sync.py sync --start-date 2023-01-01
```

7. Start the dashboard:

```bash
python3 scripts/oura_app.py
```

Open `http://127.0.0.1:8787`.

## Configuration

`config/profile.example.json` is a neutral template. Copy it to the ignored `config/user.json`, then set your timezone, targets, sleep phases, workday, training plan, and optional daily items. Items can also be added or removed from the dashboard's Data page. See [Configuration](docs/CONFIGURATION.md).

To enable the dynamic daily plan, create an OpenAI API key and add `OPENAI_API_KEY` to `.env`. The feature uses the Responses API with structured output and falls back to local rules when the key or API is unavailable.

## Commands

List supported API endpoints:

```bash
python3 scripts/oura_sync.py endpoints
```

Sync selected endpoints:

```bash
python3 scripts/oura_sync.py sync \
  --start-date 2026-01-01 \
  --endpoints daily_sleep daily_readiness daily_activity heartrate
```

Generate reports:

```bash
python3 scripts/oura_analyze.py
python3 scripts/oura_cognition_review.py
python3 scripts/oura_sleep_cycles.py
```

Run the test suite:

```bash
python3 -m unittest discover -s tests -v
```

## Data Model

```text
Oura Cloud
  -> OAuth tokens in .oura/tokens.json
  -> immutable raw snapshots in data/raw/<sync-id>/
  -> merged analysis tables in data/csv/
  -> local dashboard and Markdown reports
```

CSV files are merged by the best available stable key (`id`, `day`, `timestamp`, or `timestamp_unix`). If any request chunk for an endpoint fails, the existing CSV is preserved and the failure is recorded in the raw sync manifest.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Configuration](docs/CONFIGURATION.md)
- [Privacy and Security](docs/PRIVACY.md)
- [Deployment and Mobile Preview](docs/DEPLOYMENT.md)
- [Code Review](docs/CODE_REVIEW.md)
- [Roadmap](ROADMAP.md)
- [Contributing](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)

## Oura References

- [Oura API V2](https://cloud.ouraring.com/v2/docs)
- [OAuth authentication](https://cloud.ouraring.com/docs/authentication)
- [Export and share Oura data](https://support.ouraring.com/hc/en-us/articles/360025441594-Export-Share-Your-Oura-Data)
- [Readiness Score](https://support.ouraring.com/hc/en-us/articles/360025589793-Readiness-Score)

## License

[MIT](LICENSE)
