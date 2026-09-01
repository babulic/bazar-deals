# bazar-deals

Hunter for **small, shippable, working goods** with a conservative resale valuation.

Purchase sources:

- `vinted.sk`
- `aukro.sk`
- `bazos.sk`
- `sbazar.cz`
- Facebook Marketplace (public data only; login redirects are reported)
- `allegro.pl` and `allegro.sk` (official API token required)
- `olx.pl` (public data only; blocking is reported)

Buy-now only. Auctions and for-parts / damaged listings are excluded. eBay is not a purchase source and is not used for valuation.

**eBay is currently test-only:** `EBAY_RETENTION_ENABLED=false` blocks normal
eBay imports and reports while using the no-data-persistence exemption. Run
`python -m bazar_deals.ebay_probe` or the manual **eBay no-persistence test**
workflow to check OAuth and Browse; response data stays in memory and only
technical PASS/FAIL statuses are logged. See [access requirements](docs/automatic-marketplace-access.md).

For a real retained evaluation, the optional [private eBay store](deploy/ebay-store/README.md)
collects stock comparisons and unreviewed purchase candidates without putting
eBay data in GitHub comments, artifacts or the general comps cache. Its scheduled
workflow stays disabled until the deletion receiver has been deployed, registered
and tested and the no-persistence exemption has been removed.

Price-book gaps now trigger up to `COMPS_LIVE_QUERIES=16` targeted product searches
per hunt; stale cached prices cannot authorize BUY. Targeted buyer searches cover
every stock item before alternate names, including items late in the catalog.

## Decision rule

The old rule `listed price <= 50% of typical price` is no longer used for BUY decisions. It could produce false positives when the market value itself was overestimated.

A listing becomes **BUY only when expected conservative net profit is at least 30 EUR**.

```text
newest buy-now listing
    ↓
small + working + shoebox-scale (max 5 kg) + purchase at least 20 EUR
    ↓
identify the product from the whole ad, not the headline
(title + body + marketplace fields; AI names what the rules cannot)
    ↓
strict identity / variant matching
(storage, year, part number, lot size, phone model, Pro/Max/Plus/Mini/Ultra)
    ↓
minimum similar sample (5 ads)
    ↓
quick-sale resale value = P25 × 0.75 of similar Bazos/Aukro/Vinted asking prices
stored in `.cache/bazar-comps-v2.sqlite` and reused on the next hunt
    ↓
subtract:
  purchase price
  inbound shipping
  purchase fees (for example Vinted buyer protection)
  conservative resale-fee reserve
  known condition/accessory haircut
  seller/valuation risk reserve
    ↓
BUY only if expected net profit >= 30 EUR
```

## What is searched

The hunt looks for **small, working, fast-moving goods that fit a shoebox and weigh at most 5 kg**, priced **20–110 EUR**.

- **Bazoš** RSS rubrics: Počítače, Mobily, Elektro, Foto, Hudba, Oblečenie, Knihy, Ostatné, Dom a záhrada, Šport, Deti. Furniture, cars, motorcycles, machines, jobs, real estate, services, tickets and animals are not fetched.
- **Aukro** ~50 fast-moving shoebox categories: phones, wearables, chargers, photo/lenses, components, small appliances, flashlights, games/consoles, retro PCs, notebooks, clothing, bags, perfume, jewelry, vinyl/cassettes, comics, LEGO/figures, hiking/combat gear, coins, minerals, trading cards, merch, stamps, tools. **Christmas lights** are dropped; headlamps and ordinary lighting stay in.
- **Vinted** public catalogs: footwear, clothing, bags, jewellery, cosmetics, kids, games, phones, computers, audio, cameras, wearables, trading cards, board games, coins, books, music, tools, small kitchen — not TV, garden, bikes or winter sports. Hunt uses the public catalog JSON (`/api/v2/catalog/items`) after an anonymous homepage session, then HTML hydration as fallback. It does **not** use `VINTED_ACCESS_KEY` / `VINTED_SIGNING_KEY`; those are sell-side Pro Integrations for your own shop.

eBay is skipped on hunt. Want-to-buy ads on eBay still belong to `sell --buyers`, not to buying.

## Identification

An item can only be valued once it is known exactly what it is, so identification
reads the entire advertisement rather than the headline.

- **Every field is searched.** Title, body and the marketplace's own fields
  (eBay `shortDescription` and item specifics, Aukro category path, Vinted brand
  and size), including nested API `detail` payloads. Sellers routinely leave
  the capacity, the production year or the chip number out of the title and
  state it only in the body or in structured fields.
- **Selling boilerplate is discarded.** Without it a listing called
  `Predám, ozvite sa` produced the confident nonsense query `predam ozvite`
  instead of admitting it could not be identified.
- **Price-critical facts become a spec profile**: storage capacity, production
  year, part and model codes, lot size, phone model and variant, and known
  mineral localities/origins. Two listings must agree on these before one may
  price the other, and the facts count even when they appear only in the body.
  A capacity written in the description now rejects a comparable of a different
  capacity, which a title-only match missed. Those same facts are appended to
  the sold-comps search so a 128 GB phone and a 256 GB phone never share one
  cached P25.
- **Vague headlines do not price the item.** Matching sold comps uses the
  identified product (`iphone 13 128gb`), not `Predám telefón`.
- **Part numbers survive being written apart.** `CSG 8565 R2` yields both `8565`
  and `8565r2`, while measurements such as `220V`, `16cm` and `61g` are not
  mistaken for model codes.
- **Production year separates production runs.** An 8565R2 from 1991 is not
  priced from one made in 1993.
- **A lot is not a single piece.** `8ks kľučky` will not be priced from an ad
  selling one.
- **Mineral locality is part of identity.** Galenit from Banská Štiavnica
  (including inflected *Banskej Štiavnice* / Schemnitz) is not priced from a
  nameless specimen. "Slovensko" on a domestic ad is ignored — that is the
  seller, not the origin of the stone.

The price-book budget (`hunt.max_sold_lookups`, default 80) counts **unique
normalized product queries**, not listings. A hunt values ads from similar
listings already fetched this run plus stored rows in
`.cache/bazar-comps-v2.sqlite`. If the current batch has at least 5 similar ads,
that P25×0.75 is written to the database and reused for `COMPS_TTL_DAYS`
(default 7). The hunt does not search Bazos/Aukro/Vinted again per product —
that is what blew the GitHub Actions time cap. Ten ads for the same iPhone 13
128GB still cost one price-book write.

### AI identification

When the rules cannot name an item, the ad is handed to the free Copilot CLI
(`COPILOT_MODEL=auto`, the only mode Copilot Free and Student allow), which
reads the full text and returns a canonical name, a search query and the specs
it can quote from the ad.

This decides **what the item is, never what it is worth**. A rescued candidate
goes through exactly the same price-book valuation, the same >=30 EUR
net-profit floor and the same fail-closed AI price review as any other. Results
are cached in the comps database, so one advertisement costs one Copilot call,
and `AI_MAX_IDENTIFICATIONS` caps how many are spent per hunt.

The funnel reports `identity_ai_rescued` and `identity_ai_failed` alongside
`identity_weak`, so the value of the AI step is visible per run.

The fail-closed **price** review (also Copilot Free with `COPILOT_MODEL=auto`)
receives the same whole-advertisement text plus the extracted spec profile. It
may only lower the deterministic P25 or veto the alert; it cannot raise a value
to make a deal pass.

## Conservative valuation

`bazar-deals` intentionally prefers false negatives over false positives.

For BUY decisions:

1. Comparable items must match price-critical specifications. A 64 GB phone is not priced from 256 GB peers; a base model is not priced from a Pro/Max/Ultra variant.
2. The valuation uses the **lower quartile (P25) × 0.75** of sufficiently similar working Bazos/Aukro/Vinted asking prices, not their median and not eBay.
3. That P25×0.75 is stored in the comps SQLite database and **reused on later hunts** while it is fresh. A stale row is used when a live search finds fewer than 5 similar ads.
4. Known listing facts reduce the valuation further. Current rules include battery-health haircuts and a no-box haircut.
5. A separate risk reserve is deducted before profit is calculated.

This is deliberately stricter than normal marketplace valuation because an overvalued BUY alert costs real money while a skipped marginal deal does not.

## Net profit

Expected net profit is calculated approximately as:

```text
conservative quick-sale resale value
- purchase price
- inbound shipping
- purchase / platform fees
- conservative resale-fee reserve
- condition/accessory haircut
- risk reserve
= expected net profit
```

Default BUY floor: **30 EUR**.

## Price book

Discovered comparable prices live in:

```text
.cache/bazar-comps-v2.sqlite
```

Tables `sold_queries` (product query → P25×0.75, sample size, source, fetched_at)
and `sold_listings` (the peer ads behind that row). GitHub Actions restores and
saves this file with `actions/cache`, so the next hourly hunt starts from the
prices already found. `COMPS_TTL_DAYS` (default 7) is the reuse window.

The v2 file intentionally does not reuse the older median cache.

## Main configuration

Environment overrides used by GitHub Actions:

| Env | Default | Meaning |
|---|---:|---|
| `MIN_NET_PROFIT_EUR` | `30` | Minimum expected clean profit for BUY |
| `MIN_BUY_EUR` | `20` | Minimum purchase price; cheaper ads have no profit room |
| `MAX_BUY_EUR` | `110` | Maximum purchase price |
| `MAX_SHIPPING_EUR` | `15` | Conservative inbound shipping when actual cost is unavailable |
| `MAX_SHIPPING_CHEAP_EUR` | `11` | Shipping allowance for cheap purchases |
| `RESALE_FEE_RATE` | `0.10` | Conservative resale fee reserve |
| `SELLER_RISK_RESERVE_RATE` | `0.05` | General valuation / seller risk reserve |
| `NO_BOX_HAIRCUT_EUR` | `5` | Resale-value reduction when listing explicitly says no box |
| `COMPS_DB` | `.cache/bazar-comps-v2.sqlite` | Price book of discovered comparable prices |
| `COMPS_TTL_DAYS` | `7` | Reuse stored P25×0.75 without a live search |
| `AI_MAX_IDENTIFICATIONS` | `12` | Cap on AI identifications per hunt |

Other catalog, identity and marketplace settings remain in `src/bazar_deals/data/bazar.yaml`.

GitHub Actions uses Copilot CLI with `COPILOT_MODEL=auto`, which is compatible with Copilot Free/Student. Paid Copilot seats can override this with a specifically available model.

## Alerts

The hourly GitHub Actions hunt always comments on the Deal alerts collector
issue ([issue #1](https://github.com/babulic/bazar-deals/issues/1)). **BUY**
cards (at most 5) are ranked by expected net profit and include a clickable
listing title, asking price, usual quick-sale price, and the difference vs
usual. **Every scored ad** that is not a BUY card is listed next with the
same facts so you can open it. Ads that could not be valued (`no_sold_comps`,
fewer than 5 comparable prices) are listed under **Málo porovnateľných
inzerátov**: the title is the listing link, plus asking price, usual price
from the thin sample when `n>0`, the delta, and links to the comparable ads
that were found. The assignee is mentioned only when at least one BUY card
is present. If `scored=0`, the comment says profit was never computed
(missing price-book sample), not that every usable ad is a loss.

**Funnel** is the drop-off counter printed as `filter: usable=… scored=… buy=…`.
It is not a status headline. Each key is how many ads left that step:
fetched → usable (buy-now, 20–110 €, not bulky/damaged) → scored (had a
price-book value) → buy (expected net profit ≥ 30 €). `no_sold_comps` means
the ad was never valued. `below_net_profit` means it was valued and missed
the 30 € floor. At most `max_score_listings` (80) usable ads get a detail
fetch and a price; the rest are `score_capped`. The hourly hunt cannot open
2000 Vinted pages.

Hunt GitHub Actions is split into **Fetch Bazos / Fetch Aukro / Fetch Vinted /
Score and comment**. The yellow step is the one still running. Progress also
goes to the job summary and `hunt` notices on the run page, including a
heartbeat every 60 seconds, because `gh run view --log` cannot download logs
until the job ends.

Hunt does not use eBay keys. Selling own stock on eBay.at is a separate
`sell` command.

## Selling own stock

`hunt` finds things to buy. `sell` does the opposite: it takes the stock already
listed on the four seller accounts (bazos.sk, aukro.sk, vinted.sk, ebay.at) and
works out where the buyers are.

It now also searches **other people's want-to-buy ads** across Europe, not
only Slovak `kúpim`. The search uses the local **I will buy** verb on each
board, not just "I'm looking for":

| Language | I will buy |
|---|---|
| Slovak | kúpim |
| Czech | koupím |
| German | kaufe |
| Polish | kupię |
| Hungarian | veszek |
| Italian | compro |
| French | achète |
| Dutch | koop |

"I'm looking for" (`suche`, `szukam`, `keresek`, `cherche`, `cerco`, `zoek`)
is still searched as a second pass. Only ads whose **title is the buyer's own
dopyt** are counted as kupci. Targeted searches (willhaben, Kleinanzeigen, eBay)
that hit **your own stock titles which are actually for sale** still appear in
the digest with links, labelled as not-a-demand, so you can click them.

| Server | What is searched |
|---|---|
| bazos.sk, bazos.cz | kúpim, koupím, kaufe, kupię, veszek, compro, achète, koop |
| Aukro | same buy verbs |
| vinted.sk / .cz / .at / .de / .pl / .hu / .fr / .it / .nl / .be / .es | local I-will-buy, then looking-for |
| kleinanzeigen.de | `suche {part}` and `kaufe {part}` |
| willhaben.at | `Suche {part}` and `Kaufe {part}` |
| delcampe.net | minerals category: species+locality, then `suche` / `wanted` |
| forum64.de | C64 Kleinanzeigen search: `Suche` / `Gesucht` + part number |
| ebay.de / .at / .fr / .it / .pl / .nl / .es / .be | `{kaufe\|kupię\|veszek\|compro\|achète\|koop} {part}` plus looking-for |

Allegro PL/SK is searched through the official listing API. Sbazar.cz, OLX.pl
and Facebook Marketplace public results are also searched for buyer-authored
want-to-buy titles. Facebook groups and private/login APIs remain out of scope. Forum64 is behind Cloudflare; when GitHub Actions is blocked, the digest
says so instead of pretending the board was empty. **Zdroje** in the GitHub
comment is one line per site (`queries · rows · want-ads`), not a dump of
every search. Kleinanzeigen/willhaben/Delcampe/Forum64 stop after HTTP 403
or Cloudflare instead of retrying every stock query.

When a dopyt matches an inventory item, GitHub Actions (`.github/workflows/sell.yml`,
hourly at minute 30 and on every push to `main`) posts a digest on a **Sell buyers** issue — not hunt
issue #1 — pairing the buyer (where, title, identification, offered price if
stated) with your own listings for that product.

```powershell
python -m bazar_deals sell
python -m bazar_deals sell --refresh
python -m bazar_deals sell --buyers
python -m bazar_deals sell --buyers --notify
python -m bazar_deals sell --segment minerals
python -m bazar_deals sell --format json
```

`--refresh` pages through every seller account before planning: Bazos by search
offset until the reported total is reached, Aukro by `totalPages` through the
public offer search, eBay and Vinted through their official APIs. Results are
written to `.cache/sell-inventory.yaml`, which then takes precedence over the
committed snapshot.

Bazos and Aukro need no credentials. eBay blocks datacentre HTML requests, so it
needs `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET`; Vinted renders its item grid by
infinite scroll behind DataDome, so it needs `VINTED_ACCESS_KEY` and
`VINTED_SIGNING_KEY` from Pro Integrations. A source that cannot be collected is
reported as skipped and keeps its previous prices, so a missing credential is
never mistaken for a delisted item.

It reports, per item:

- which channels reach which buyer countries, and which target countries no live
  channel reaches at all,
- what Packeta actually costs to each destination versus what the listing
  charges today,
- whether the postage is small enough relative to the price for a cross-border
  sale to make sense,
- a title per channel in the buyer's language that fits that platform's
  character budget.

Titles are rebuilt from structured fields rather than translated, so mineral
species, part numbers and the historic German and Hungarian locality names that
collectors search are present in every language. Character limits live in
`selling.title_limits`; Bazos (60) and eBay (80) are confirmed by mid-word
truncation in the live listings, Vinted and Aukro are the longest observed.

Packeta prices in `selling.packeta` are public list prices. A business contract
is cheaper, so override them with the real rates before trusting a margin.

The reasoning behind the channel choices is in
[`docs/predaj-strategia.md`](docs/predaj-strategia.md).

## Run

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m bazar_deals hunt --offline --source bazos
python -m bazar_deals hunt --notify
python -m bazar_deals sell
```

## Additional marketplaces and Slovakia delivery

**Access correction (2026-08-31):** Adding an Allegro token is not sufficient
for a new application. Allegro support says new `/offers/listing` verification
has stopped; legacy access depends on individual agreements. OLX's standard
API only manages the authenticated user's own ads. Facebook login does not
by itself establish unattended data access. The adapters below are partial
integration support, not five verified live production sources. See the
[concrete access and deployment plan](docs/marketplace-access-plan.md).


`hunt --source sbazar|facebook|allegro_pl|allegro_sk|olx` selects one new source;
`hunt --source all` includes all five. The hourly workflow fetches each source
separately and preserves source errors in the final report. Searches are bounded
by `central_europe.queries` and `max_queries` in the packaged YAML; these are a
small initial set of product searches, not an exhaustive scan of each site.
`sell --buyers` uses the same sources with Czech, Slovak or Polish buy verbs.
This does not create seller accounts, publish listings, send messages, or import
new seller-account inventory.

For every new purchase source, **unknown delivery to Slovakia means no BUY**.
A `.sk` domain, seller location, mention of Slovakia, OLX delivery badge, or
ordinary domestic postage is insufficient. The source must provide either an
explicit affirmative delivery statement in the ad, country-wide SK shipping
metadata, or an official Allegro search result filtered by `shipping.country=SK`.
Negated/conditional statements do not qualify. This deliberately misses ads whose
seller would agree only after being contacted. The pipeline also rechecks price,
availability and shipping after fetching a shortlisted detail page.

- **Sbazar:** parses the public Astro listing data and item details. Search-page
  ads normally omit the description, so delivery is checked on the detail page.
- **Allegro PL/SK:** set `ALLEGRO_ACCESS_TOKEN` for an application allowed to use
  `GET /offers/listing`. Both markets request SK delivery, EUR and BUY_NOW.
  Missing/expired tokens and rejected access are reported; no private API or
  CAPTCHA bypass is used. The lowest displayed postage is not assumed to be SK
  postage; the conservative shipping reserve remains when the actual cost is
  unknown. The same offer on both domains counts once in the price-book sample.
- **OLX/Facebook:** scheduled runs use manual mode. Public Product/Offer
  structured data is supported for explicit diagnostics. Login,
  redirects, HTTP 403/429 or unreadable results are reported as unavailable,
  with a manual-search link. These sources are **not guaranteed to work
  unattended**. The verification on 2026-08-31 returned OLX HTTP 403 and a
  Facebook login redirect; no live results were claimed for either site.
- **CZK and PLN:** online scoring, buyer search and inventory refresh load both
  rates from one dated ECB snapshot automatically. `EUR_CZK` / `EUR_PLN` are
  optional manual overrides, not required configuration. GitHub Actions caches
  `.cache/ecb-fx.json` across runs; repository variables with those names can
  override individual currencies. `ALLEGRO_ACCESS_TOKEN` remains unrelated
  authentication configuration.


The existing domestic Bazos/Aukro/Vinted behavior is retained; an explicit
`ships_to_slovakia=false` is now rejected across all sources. The stronger
positive-evidence requirement applies to the five new sources and the existing
eBay delivery gate. All normal item, identity, profit and AI-review gates remain.

Official references: [Allegro destination/currency search parameters](https://developer.allegro.pl/news/serwisy-zagraniczne-allegro-dodalismy-nowe-parametry-wyszukiwania-na-liscie-ofert-x565R2W36Iw),
[OLX API delivery documentation](https://developer.olx.pl/api/doc),
[Facebook delivery availability](https://www.facebook.com/help/796066857221106?locale=en_GB).

Offline tests isolate `Settings` from the real `.env` and process credentials.
Use `python -m pytest` and `python -m bazar_deals hunt --offline --source bazos`.

## Automatic CZK/EUR and PLN/EUR exchange rates

The online CLI checks the [ECB daily XML](https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml)
once per UTC calendar day and stores the publication date, check date and both
rates in `FX_CACHE` (default `.cache/ecb-fx.json`). Prices and known postage use
`EUR = foreign amount / rate`. Hunt, comparable prices, buyer-demand budgets and
Aukro inventory refresh share the same rates; the previous separate fixed Aukro
rate and rounding to whole euros have been removed.

`FX_MAX_AGE_DAYS` defaults to 7 calendar days, measured from ECB publication, not
cache download. Weekends therefore work without inventing a new rate. On network
or XML errors a still-valid cache is used; future/stale/missing rates leave the
currency unpriced. Such purchases cannot reach BUY. Buyer demand can retain an
unknown budget; a failed inventory conversion preserves the old inventory.
FX provenance and failure notes are included in hunt and buyer reports.

`--offline` never fetches ECB; it uses only valid cached rates or explicit
`EUR_CZK` / `EUR_PLN` overrides. `Settings()` itself never accesses the network.
Library callers outside the CLI can call `prepare_exchange_rates(settings)` and
pass the returned settings to their operations. Both overrides must be positive
finite values, expressed in units of the foreign currency per EUR. Leave them
blank for automatic rates. A manual override has no automatic freshness guarantee
and is labelled as manual in the report.

ECB reference rates are indicative, not guaranteed card/payment-provider rates.
A configurable `FX_FEE_RATE` (default `0.02`) adds a 2% reserve to CZK/PLN
purchase prices and postage, rounded up to cents and included in `fees`
(`fx_fee_reserve` identifies that part). Foreign comparable prices and buyer
budgets are reduced by the reserve, rounded down. EUR amounts incur no FX
reserve. Inventory refresh retains the converted advertised price rather than
treating it as net proceeds. Use actual payment-provider costs to tune this estimate.

## Manual import for blocked marketplaces

Scheduled hunts and buyer searches keep Facebook/OLX in manual mode and skip
Allegro without an authorized token. They do not repeatedly probe blocked pages.
Status notes distinguish `READY` (readable source, not a BUY), `LOGIN_REQUIRED`,
`BLOCKED`, `ACCESS_NOT_GRANTED`, `STALE_FX`, and
`NEEDS_DELIVERY_CONFIRMATION`. Public-page parsers remain available for explicit
diagnostic probes; a successful login is not permission for unattended collection.

Save selected offers locally as JSON (array of objects) or CSV (same field names).
Keep private evidence in `.cache/`, which is ignored by Git. Example:

```json
[{
  "marketplace": "olx",
  "external_id": "replace-with-offer-id",
  "kind": "offer",
  "title": "Nintendo Switch V2",
  "description": "Replace with the actual condition and contents of the selected offer.",
  "url": "https://www.olx.pl/d/oferta/replace-with-real-offer.html",
  "price": "215.00",
  "currency": "PLN",
  "available": false,
  "checked_at": "2026-08-31T12:00:00+02:00",
  "fulfillment": "unknown",
  "fulfillment_cost": null,
  "fulfillment_currency": "EUR",
  "evidence": ""
}]
```

Replace every placeholder from the actual ad. `available` means confirmed
currently available at that fixed price. Use `delivery_sk` or `pickup_sk` only
when confirmed, with a factual `evidence` description and total shipping or
pickup/travel cost. Zero is allowed only when that cost is genuinely zero.
`checked_at` must include a timezone; confirmations expire after **24 hours**.
Future dates, unknown fulfillment, missing costs and unavailable offers cannot
reach BUY. Manual evidence is preserved instead of overwritten by a public-page
fetch. All valuation, condition and configured AI checks still apply. Recheck the
listing before actually buying; the program never places orders.

```bash
# Validate and normalize without network access:
python -m bazar_deals import --manual-in .cache/selected.json --listings-out .cache/normalized.json
# Score selected offers, refreshing FX and using the normal price/AI checks:
python -m bazar_deals hunt --manual-in .cache/selected.json
# Import genuine wanted ads (kind="wanted", title must say kúpim/koupím/kupię/WTB):
python -m bazar_deals sell --buyers --manual-in .cache/wanted.csv
# Match only local demands, without contacting marketplaces or ECB:
python -m bazar_deals sell --buyers --manual-in .cache/wanted.csv --offline
```

Hunt scores only the supplied manual files plus any `--listings-in` files.
Sell adds manual wanted ads to live searches unless `--offline` is supplied.
A wanted ad is a lead, not a confirmed sale or delivery agreement; inactive or
older-than-24-hour imported demands are excluded. CSV booleans use `true`/`false`.
Only the selected marketplace's HTTPS domain is accepted. Import does not fetch
URLs, read browser sessions, message sellers or publish inventory. Local imports
are **not automatically uploaded into GitHub Actions**.

Hourly alerts no longer run the destructive issue-comment cleanup workflow.
The separate manually triggered maintenance workflow is unchanged.

The production price-book cache uses a new `sold-comps-fx-v3-` key so legacy
valuations without the FX reserve are not restored on rollout. Local users can
select a fresh `COMPS_DB` path to rebuild their existing price book as well.

For unattended Allegro authentication, an **already entitled** application can
supply `ALLEGRO_CLIENT_ID`, `ALLEGRO_CLIENT_SECRET` and
`ALLEGRO_LISTING_ACCESS_CONFIRMED=true`. The client obtains an application token,
reuses it until shortly before expiry, and retries a listing request once after
401 with a new token. It never retries 403 by refreshing credentials. Tokens stay
in process memory; OAuth errors omit response bodies. Both scheduled workflows
read credentials from GitHub Secrets and the confirmation flag from a repository
variable. Missing credentials/entitlement leave the source manual.

See [exact access requirements and unsent support requests](docs/automatic-marketplace-access.md).
New credentials alone do not unlock Allegro, OLX or Facebook market-wide search.
