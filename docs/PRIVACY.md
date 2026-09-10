# Privacy and Security

Oura data can reveal sleep timing, heart rate, workouts, stress, illness signals, location-adjacent routines, and medication habits. Treat the repository's ignored files as sensitive health records.

## Threat Model

This project protects against accidental publication through Git and casual access by other local accounts. It does not currently protect against malware, a compromised user account, an unlocked computer, or intentional exposure of the local server to a network.

## Local Protections

- OAuth tokens are stored in `.oura/tokens.json` with mode `0600`.
- Tokens, raw payloads, CSV files, local profiles, logs, exports, and reports are ignored by Git.
- The dashboard binds to `127.0.0.1` by default.
- Non-local binding requires a password; temporary public previews must use HTTPS.
- Static responses use a restrictive content security policy and disable MIME sniffing.
- Writes use atomic replacement to reduce corruption after interruption.

## Before Publishing

Run all three checks:

```bash
git status --short
git check-ignore -v .env .oura/tokens.json data/csv/sleep.csv config/user.json data/daily_item_log.json
git grep -nE 'access_token|refresh_token|client_secret|Bearer '
```

Inspect every staged file with `git diff --cached`. Never add ignored files with `git add -f`.

## Safe Sharing

- Share source code, never your `.env`, token file, personal profile, raw API snapshots, CSVs, reports, or daily item log.
- Use generated or heavily aggregated sample data for screenshots.
- Revoke the Oura application credentials immediately if a secret is exposed.
- Avoid publishing exact sleep and wake timestamps without considering routine privacy.
- Stop temporary tunnels as soon as mobile testing is complete.

## Optional AI Data Transfer

The AI plan is disabled unless `OPENAI_API_KEY` is configured. When enabled, the server sends the latest-night metrics, recent aggregate trends, schedule constraints, and unnamed medication time slots to the OpenAI Responses API. It does not send raw Oura rows, OAuth credentials, medication names, doses, profile names, or contact details. Requests set `store: false`; review OpenAI's current API data controls before enabling the feature.

## Medical Boundary

The software summarizes wearable estimates and personal observations. It must not be used to diagnose conditions, adjust medication, or replace professional medical care. User-entered daily items are reminders only, not product recommendations or interaction guidance.
