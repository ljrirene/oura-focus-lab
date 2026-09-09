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
- `schedule.phases`: wind-down, bed, lights-out, wake, and phase length
- `schedule.workday`: the timeline shown in the Schedule view
- `schedule.weekPlan`: the training plan shown in the Schedule view
- `targets`: sleep duration, Readiness, Sleep Score, and optional HRV reference
- `dailyItems`: user-defined medication, supplement, or other check-offs

Daily items can be managed from the dashboard's Data page. The app assigns stable IDs and saves completion state to `data/daily_item_log.json`; both files stay local. There are no checked-in medication or supplement defaults.

The recovery action thresholds in `recovery_status()` are product heuristics. They are not medical rules and should be changed cautiously.

## Storage Overrides

The sync CLI supports `--raw-dir`, `--csv-dir`, and `--token-file`. Keep all destinations outside cloud-synced folders unless you have consciously enabled encryption and access controls.
