# Configuration

Copy `.env.example` to `.env`. The `.env` file is ignored by Git.

## OAuth

| Variable | Required | Description |
|---|---:|---|
| `OURA_CLIENT_ID` | Yes | Oura OAuth application client ID |
| `OURA_CLIENT_SECRET` | Yes | Oura OAuth application client secret |
| `OURA_REDIRECT_URI` | No | Defaults to `http://localhost:8765/callback` |
| `OURA_SCOPES` | No | Space-separated scopes; defaults are defined in `oura_sync.py` |
| `OURA_REQUEST_TIMEOUT_SECONDS` | No | Network timeout, default `30` |

Use the smallest set of scopes that supports your analysis. For sleep-only work:

```bash
python3 scripts/oura_sync.py auth --scopes daily
```

## Local Profile

Create the private profile once:

```bash
cp config/profile.example.json config/user.json
```

`config/user.json` is ignored by Git. It controls:

- `timezone`: an IANA timezone used by calendar events
- `schedule.startDate`: the first day of the staged schedule
- `schedule.phases`: private sleep timing constraints used by local rules and AI planning
- `schedule.workday`: private workday constraints used by local rules and AI planning
- `schedule.weekPlan`: private training constraints used by local rules and AI planning
- `targets`: sleep duration, Readiness, Sleep Score, and optional HRV reference
- `dailyItems`: user-defined medication, supplement, or other check-offs
- `sync`: automatic sync interval, recent lookback window, and selected endpoints

Daily items can be managed from the dashboard's Data page. The app assigns stable IDs and saves completion state to `data/daily_item_log.json`; both files stay local. There are no checked-in medication or supplement defaults.

The recovery action thresholds in `recovery_status()` are product heuristics. They are not medical rules and should be changed cautiously.

Automatic sync defaults to every 60 minutes and requests only the most recent 3 days. The minimum interval is 15 minutes and the maximum lookback is 14 days. Historical CSV rows are preserved by the incremental merge.

## Public Preview Authentication

Set these only when exposing the local server through an HTTPS tunnel:

```dotenv
OURA_APP_USERNAME=oura
OURA_APP_PASSWORD=replace-with-a-long-random-password
```

The server refuses a non-local bind without `OURA_APP_PASSWORD`. Basic Auth must only be used behind HTTPS.

## AI Daily Plan

Add the API key only to the ignored `.env` file:

```dotenv
OPENAI_API_KEY=your-project-key
OPENAI_MODEL=gpt-5-mini
OPENAI_TRANSCRIBE_MODEL=whisper-1
```

After each successful Oura sync, the app compares the current planning context with the cached plan. It calls the AI again only when the date or wearable summary has changed; otherwise it reuses the cache. The UI can also request a manual regeneration. Plans must cover wake time through lights out; server validation adds dinner and bedtime anchors if the model omits them. The AI may adjust work, exercise, breaks, learning, and sleep preparation, but validation keeps the configured wake time fixed and rejects sleep shifts beyond 60 minutes. Medication names, doses, and instructions are not sent in the planning context or controlled by the model.

The Review view can record up to 90 seconds. The browser sends audio to the local server, which calls the configured transcription model and then the Responses API for a concise adherence review. Raw audio is not written to disk. The transcript and generated review are saved only in ignored `data/voice_reviews.json`.

## Storage Overrides

The sync CLI supports `--raw-dir`, `--csv-dir`, and `--token-file`. Keep all destinations outside cloud-synced folders unless you have consciously enabled encryption and access controls.
