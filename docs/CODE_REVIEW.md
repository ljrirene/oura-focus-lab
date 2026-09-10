# Code Review

Review date: 2026-09-09

## Resolved Findings

### P1: Partial sync could overwrite complete history

`command_sync()` wrote a CSV even when one or more request chunks failed. A failed or partial request could therefore replace a complete local table. CSVs are now updated only when every chunk for the endpoint succeeds; otherwise the existing file is preserved and the raw manifest records the failure.

### P1: Short sync ranges could erase older rows

Every successful sync previously replaced the endpoint CSV with only the requested date range. Successful rows are now merged into the prior table using the best available stable key, with newly fetched rows replacing matching records.

### P1: OAuth refresh could discard the refresh token

Some OAuth servers omit unchanged fields from refresh responses. The code previously replaced the complete token document with that response. It now retains the old refresh token, scope, and token type when they are omitted.

### P2: Interrupted writes could corrupt local state

Token JSON, endpoint CSVs, profiles, and daily item logs were written directly to their destination. They now use a temporary sibling file and atomic replacement.

### P2: Daily item API accepted malformed JSON shapes

A JSON array or empty body could produce an unhandled exception or block a request. The API now enforces a 2-4000 byte object body, validates item categories, and rejects unknown item IDs.

### P2: Calendar timezone was hard-coded

Calendar events previously used a fixed timezone. Timezone and schedule content now come from the ignored local profile.

### P1: Personal defaults were present in tracked source

Schedule details and named daily items were embedded across Python, JavaScript, HTML, and documentation. They have been replaced by `config/profile.example.json`; personal values live only in ignored `config/user.json` and are managed through the local UI.

### P2: No automated regression suite

Thirteen standard-library tests now cover token preservation, private permissions, CSV merging, failed-sync preservation, daily item validation, schedule phases, calendar timezone, rolling sync scope, preview authentication, AI response extraction, immutable wake-time validation, and medication-name exclusion.

## Remaining Risks

### P2: CSV schema changes are not versioned

Oura may add or rename API fields. The flattener tolerates new fields, but there is no schema version, migration command, or fixture-based contract test.

### P3: Calendar output is minimally implemented

The generated ICS works for the current event text but does not yet implement UTF-8 line folding or full RFC 5545 escaping.

### P3: Analysis thresholds are heuristic

The cycle detector and automatic review thresholds are transparent but not externally validated. Reports correctly describe them as exploratory.

## Verification

```text
python3 -m unittest discover -s tests -v  -> 13 passed
python3 -m py_compile scripts/*.py       -> passed
node --check app/app.js                  -> passed
```
