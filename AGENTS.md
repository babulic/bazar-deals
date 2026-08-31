# AGENTS.md

## Repository instructions

`bazar-deals` is a Python 3.11+ CLI package; hunts are one-shot batch commands. An optional private eBay retention/deletion service is documented in `deploy/ebay-store/README.md`; it is not needed for normal local CLI work. Use `README.md` and `pyproject.toml` as the primary command reference.

- Install development dependencies with `python -m pip install -e ".[dev]"`.
- Run the CLI as a module: `python -m bazar_deals hunt ...`.
- Run the offline test suite with `python -m pytest`; tests use fixtures under `tests/`.
- Use `python -m bazar_deals hunt --offline --source bazos` for a network-free smoke run.
- A live hunt can access Bazos, Vinted, Aukro, and eBay. eBay purchasing is disabled unless `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` are configured because delivery to Slovakia must be verified through the Browse API.
- GitHub Actions uses Copilot CLI `auto` selection for the required final AI review, which is compatible with Copilot Free. OpenAI remains an optional local provider; do not assume an `OPENAI_API_KEY` is present.
- `--notify` writes GitHub issue comments and requires `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and `GITHUB_ALERT_ISSUE`; omit it during ordinary local testing.
- Conservative sold comps and approved AI price reviews are cached at `.cache/bazar-comps-v2.sqlite` by default (`COMPS_DB`).
- Catalog and marketplace defaults live in `src/bazar_deals/data/bazar.yaml`; environment overrides go in `.env` (see `.env.example`).
- No separate lint or type-check command is configured.
