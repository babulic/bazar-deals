# Update and validation — 2026-08-31

All three configured local projects were fetched from origin. Existing IDE files,
local environment files and other untracked work were preserved. No force pull,
reset or stash was used. Publication of the bazar changes follows the approved PR plan.

| Project | Upstream revision | Result |
|---|---|---|
| bazar-deals | `e3d0c86` | Fast-forwarded; 188 original tests pass after isolating local settings; 272 tests pass with marketplace, FX and manual-import changes |
| polymarket-tracker | `f8d7711` | Merged with local `27ccd35` into `e5c2fab`; 52 tests, type checks and TypeScript build pass |
| peer-order-finder | `948847f` | Already current; 38 tests pass |

## Reviewed changes

Bazar-deals now caps scoring at 80 listings after distributing candidates across
marketplaces. This bounds detail requests while preserving the full fetched
batch as price-book input. The new upstream tests cover fairness and the cap.

Polymarket changes trade aggregation and fingerprint buckets to the configured
60-minute window, updates notification text, uses the same 30-day horizon for
market discovery and TOP 10, and aligns fallback explanations. The discovery
test uses a relative date so it does not expire. Merge conflicts were resolved
in favour of the new fallback implementation and the local Alwyzon deployment
documentation. The merged checkout differs from upstream only in deployment
documentation; the deployed application source matches the tested source.

Peer's latest changes centralize orderbook query options, minimum liquidity,
control-diagnostic limits and currency reset configuration, with validation of
required fields. They affect the JS clients and the PHP scanner. The Node test
suite passes; no live wallet login or Create Order action was performed.

## Polymarket production deployment

- Target: existing Alwyzon `/opt/polymarket-tracker` installation.
- Online SQLite backup: `data/backups/pre-deploy-20260831.db`.
- Previous image preserved as `polymarket-tracker:pre-20260831`.
- Fast-forwarded production to `f8d7711`, built with Docker Compose, then
  recreated the tracker without replacing the database or environment file.
- Git initially failed under sudo because it used the wrong known-hosts file;
  the fetch then used the existing deployment key and deployment user's
  known-hosts file with strict host verification still enabled.
- Verified Docker healthy, HTTP health `ok=true`, WebSocket `open`, 24 selected
  markets, 60-minute lookback, 30-day horizon, 10-market ranking and resumed
  price-point collection. Existing issue-mode notification resumed naturally.
- No separate deploy-test comment was sent and no trades were placed.

## Bazar additions and remaining access limits

The five sources are registered for `hunt` and `sell --buyers`, with bounded
searches, public-page/API parsing, source-health notes preserved across staged
fetch/score runs, and new workflow steps. New purchases require affirmative SK
delivery evidence. Dopyt/WTB ads cannot become purchases. Unknown PLN rates do
not become EUR, post-enrichment prices are converted again, and identical
Allegro offers across PL/SK count once as comparables.

Live verification:

- Sbazar search HTTP 200: 58 parsed ads. A public detail page parsed correctly.
  These samples did not prove SK delivery and were not treated as purchasable.
- OLX public search: HTTP 403; inaccessible, not an empty marketplace.
- Facebook public search: HTTP 302 redirect; no authenticated data accessed.
- Allegro: local token is absent. Request contract is covered by mocked tests;
  a live authenticated search has not been verified.

Allegro is not available locally or in GitHub Actions Secrets. A token alone
is insufficient without authorized listing access. Scheduled Facebook/OLX scans
are explicitly manual; no cookies, CAPTCHA workarounds or proxy rotation were
added. Sbazar remains public. The latest explicit probes and FX result are
recorded below.

Additional implementation:

- Both CZK/EUR and PLN/EUR refresh from one dated ECB snapshot, cached daily.
  Invalid/stale/future snapshots fail closed; manual overrides remain optional.
- FX_FEE_RATE defaults to 2%; foreign purchase/postage costs increase, comparable
  proceeds and buyer budgets decrease. Currency provenance survives conversion.
- JSON/CSV manual imports distinguish sale offers from wanted ads. Delivery to
  Slovakia or pickup in Slovakia requires timestamped evidence and explicit
  fulfillment costs. Confirmation expires after 24 hours; stale/unavailable
  offers cannot become BUY. Manual imports remain local and private.
- Offline buyer matching makes no network requests. Imported wanted ads must be
  current and contain genuine WTB intent; sale offers do not become buyer leads.
- Scheduled runs report manual/blocked sources, rather than retrying them.
- Hunt no longer invokes destructive issue-comment cleanup.
- Tests isolate .env and process credentials. IDE files are excluded from commits.

Validation before publication: 272 offline tests pass, Bazos offline CLI smoke
passes, git diff --check passes. The publication PR and GitHub Actions provide
the authoritative deployment status; changes only reach scheduled jobs on main.
The Polymarket merge remains a local documentation merge and was not pushed.

Latest explicit live probe: ECB 2026-08-28, 1 EUR = 24.148 CZK and 4.3365 PLN;
Sbazar 58 readable offers (none confirmed for SK in the search response),
OLX BLOCKED / HTTP 403, Facebook LOGIN_REQUIRED / redirect. No messages sent.
