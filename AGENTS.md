# AGENTS.md

## Cursor Cloud specific instructions

`bazar-deals` is a single Python (>=3.11) CLI package — no web server, database server, or long-running services. Everything runs as a one-shot `hunt` batch command. Standard commands live in `README.md` and `pyproject.toml`; the notes below only cover non-obvious caveats.

- The update script installs the package in editable mode with dev extras (`pip install -e ".[dev]"`) into the user site-packages, so `pytest` and the package are importable without activating a virtualenv.
- Run the CLI as a module: `python3 -m bazar_deals hunt ...`. The `bazar-deals` console script is installed to `~/.local/bin`, which is not on `PATH` by default, so prefer the module form.
- Tests: `python3 -m pytest` (fully offline, uses fixtures under `tests/`).
- Hello-world / smoke run (no network needed): `python3 -m bazar_deals hunt --offline --source bazos`. `--offline` uses bundled fixtures in `tests/fixtures/`; without it the CLI makes live outbound HTTPS calls to bazos.sk / ebay.de / aukro.sk / vinted.sk which may be blocked or rate-limited.
- No lint tooling is configured in the repo (no ruff/mypy/flake8/black config), despite `.gitignore` mentioning cache dirs. Do not assume a lint command exists.
- `--notify` posts to a GitHub issue and requires `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and `GITHUB_ALERT_ISSUE`; leave it off for local testing.
- Sold-comps are cached in SQLite at `.cache/bazar-comps.sqlite` (`COMPS_DB`), created automatically on first run.
- Config defaults live in `src/bazar_deals/data/bazar.yaml`; secrets/env overrides go in `.env` (see `.env.example`).
