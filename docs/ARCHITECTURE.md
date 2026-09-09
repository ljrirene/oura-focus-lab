# Architecture

## Goals

Oura Focus Lab is intentionally local-first and dependency-light. It separates acquisition, storage, analysis, and presentation so personal health data does not need to leave the user's machine after it is downloaded from Oura.

## Components

| Component | Responsibility |
|---|---|
| `scripts/oura_sync.py` | OAuth, token refresh, pagination, raw snapshots, CSV merge |
| `scripts/oura_app.py` | Local HTTP server, dashboard model, profile and daily item storage, calendar export |
| `app/` | Static HTML, CSS, and browser JavaScript |
| `scripts/oura_analyze.py` | Coverage, recent metrics, exploratory correlations |
| `scripts/oura_cognition_review.py` | Longitudinal sleep, activity, stress, and next-day comparisons |
| `scripts/oura_sleep_cycles.py` | Conservative cycle estimates from Oura hypnograms |

## Data Flow

1. `auth` opens the Oura OAuth authorization URL and listens only on localhost for the callback.
2. Tokens are written atomically to `.oura/tokens.json` with owner-only file permissions.
3. `sync` requests each endpoint in bounded date chunks and saves a raw JSON snapshot.
4. A CSV is updated only after every chunk for that endpoint succeeds.
5. New rows are merged with prior rows by a stable key, with the new version winning.
6. The dashboard reads local CSVs on demand and returns a compact JSON view model.

## Local HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/dashboard` | Latest status, trends, comparisons, schedule, experiment |
| `GET` | `/api/profile` | Local profile used by the dashboard |
| `GET` | `/api/daily-items?date=YYYY-MM-DD` | User-defined items and completion state |
| `POST` | `/api/items` | Add a medication, supplement, or other item |
| `DELETE` | `/api/items?id=ITEM_ID` | Remove a configured item |
| `POST` | `/api/daily-item` | Toggle one configured item for a date |
| `GET` | `/api/reminders.ics` | Download calendar reminders |

The server defaults to `127.0.0.1`. Binding it to a public interface is unsupported because the API has no user authentication.

## Analysis Boundaries

- Readiness and Sleep scores follow Oura's 0-100 scale.
- Correlations are descriptive and unadjusted; they are not causal estimates.
- The dashboard uses sleep and recovery as conditions that may support cognition. It does not measure cognition.
- Sleep stages and cycle boundaries are wearable estimates, not polysomnography.
- Schedule content comes from an ignored local profile. The checked-in profile is neutral example data.

## Reliability Decisions

- Token and CSV writes use temporary files followed by atomic replacement.
- A partial endpoint sync never replaces the last complete CSV.
- OAuth refresh responses retain the prior refresh token when rotation is omitted.
- Unknown `/api/` routes return JSON 404 responses rather than the SPA shell.
- Daily item categories are allow-listed, item IDs must exist in the local profile, and request bodies are size-limited.

## Compatibility

The runtime uses the Python standard library and browser APIs. CI tests Python 3.10 and 3.12; the current implementation also runs on the author's Python 3.8 environment.
