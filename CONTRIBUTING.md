# Contributing

Contributions are welcome, especially around privacy, data integrity, accessibility, configuration, and reproducible analysis.

## Development

The project has no runtime dependencies beyond Python and a modern browser.

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py
node --check app/app.js
python3 scripts/oura_app.py
```

## Pull Requests

- Keep personal health data and credentials out of commits.
- Add or update tests for behavioral changes.
- Preserve the local-first default and localhost binding.
- Label heuristic analysis clearly and avoid causal or medical claims.
- Keep new dependencies optional unless they solve a substantial problem.
- Update the architecture and configuration docs when interfaces change.

## Test Data

Use synthetic fixtures only. Do not redact and commit a real export: timestamps and combinations of fields can still identify routines or health events.

## Style

- Python: standard library patterns, explicit types, small pure functions where practical.
- JavaScript: dependency-free browser APIs and accessible DOM semantics.
- UI copy: short, concrete, and action-oriented.
