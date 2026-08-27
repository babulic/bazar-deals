# bazar-deals

Hunter for **small, shippable, working goods** with a conservative resale valuation.

Purchase sources:

- `vinted.sk`
- `aukro.sk`
- `bazos.sk`
- `ebay.de` — only listings for which delivery to Slovakia is confirmed by the eBay Browse API

Buy-now only. Auctions and for-parts / damaged listings are excluded.

## Decision rule

The old rule `listed price <= 50% of typical price` is no longer used for BUY decisions. It could produce false positives when the market value itself was overestimated.

A listing becomes **BUY only when expected conservative net profit is at least 30 EUR**.

```text
newest buy-now listing
    ↓
small + working + within purchase cap
    ↓
for ebay.de: deliveryCountry=SK must be confirmed
    ↓
identify the product from the whole ad, not the headline
(title + body + marketplace fields; AI names what the rules cannot)
    ↓
strict identity / variant matching
(storage, year, part number, lot size, phone model, Pro/Max/Plus/Mini/Ultra)
    ↓
minimum sold sample
    ↓
quick-sale resale value = P25 of similar working ebay.de SOLD comps
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

## Identification

An item can only be valued once it is known exactly what it is, so identification
reads the entire advertisement rather than the headline.

- **Every field is searched.** Title, body and the marketplace's own fields
  (eBay `shortDescription`, Aukro category path, Vinted brand and size). Sellers
  routinely leave the capacity, the production year or the chip number out of
  the title and state it only in the body.
- **Selling boilerplate is discarded.** Without it a listing called
  `Predám, ozvite sa` produced the confident nonsense query `predam ozvite`
  instead of admitting it could not be identified.
- **Price-critical facts become a spec profile**: storage capacity, production
  year, part and model codes, lot size, phone model and variant. Two listings
  must agree on these before one may price the other, and the facts count even
  when they appear only in the body. A capacity written in the description now
  rejects a comparable of a different capacity, which a title-only match missed.
- **Part numbers survive being written apart.** `CSG 8565 R2` yields both `8565`
  and `8565r2`, while measurements such as `220V`, `16cm` and `61g` are not
  mistaken for model codes.
- **Production year separates production runs.** An 8565R2 from 1991 is not
  priced from one made in 1993.
- **A lot is not a single piece.** `8ks kľučky` will not be priced from an ad
  selling one.

### AI identification

When the rules cannot name an item, the ad is handed to the free Copilot CLI
(`COPILOT_MODEL=auto`, the only mode Copilot Free and Student allow), which
reads the full text and returns a canonical name, a search query and the specs
it can quote from the ad.

This decides **what the item is, never what it is worth**. A rescued candidate
goes through exactly the same completed-sales valuation, the same >=30 EUR
net-profit floor and the same fail-closed AI price review as any other. Results
are cached in the comps database, so one advertisement costs one Copilot call,
and `AI_MAX_IDENTIFICATIONS` caps how many are spent per hunt.

The funnel reports `identity_ai_rescued` and `identity_ai_failed` alongside
`identity_weak`, so the value of the AI step is visible per run.

## Conservative valuation

`bazar-deals` intentionally prefers false negatives over false positives.

For BUY decisions:

1. Comparable items must match price-critical specifications. A 64 GB phone is not priced from 256 GB peers; a base model is not priced from a Pro/Max/Ultra variant.
2. The valuation uses the **lower quartile (P25)** of sufficiently similar working sold comps, not their median.
3. Current asking prices from Bazoš/Aukro/Vinted/eBay may be collected as a deliberately haircutted fallback. They can create only a provisional candidate and **never create BUY without the required fail-closed AI web verification**. With AI disabled or unavailable, asking-only candidates remain SKIP.
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

## eBay Germany

Live eBay purchasing requires `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET`.

The Browse API search includes `deliveryCountry:SK`. Public eBay search HTML is not accepted as a purchase source because it cannot reliably prove that the specific listing ships to Slovakia. When the API exposes a shipping cost, that cost is used; otherwise the conservative configured shipping allowance is used.

## Sold comps cache

Conservative sold valuations use a new cache database:

```text
.cache/bazar-comps-v2.sqlite
```

The v2 cache intentionally does not reuse the older median/asking-price cache, preventing stale inflated values from leaking into the new decision model.

## Main configuration

Environment overrides used by GitHub Actions:

| Env | Default | Meaning |
|---|---:|---|
| `MIN_NET_PROFIT_EUR` | `30` | Minimum expected clean profit for BUY |
| `MIN_BUY_EUR` | `10` | Minimum purchase price |
| `MAX_BUY_EUR` | `110` | Maximum purchase price |
| `MAX_SHIPPING_EUR` | `15` | Conservative inbound shipping when actual cost is unavailable |
| `MAX_SHIPPING_CHEAP_EUR` | `11` | Shipping allowance for cheap purchases |
| `RESALE_FEE_RATE` | `0.10` | Conservative resale fee reserve |
| `SELLER_RISK_RESERVE_RATE` | `0.05` | General valuation / seller risk reserve |
| `NO_BOX_HAIRCUT_EUR` | `5` | Resale-value reduction when listing explicitly says no box |
| `COMPS_DB` | `.cache/bazar-comps-v2.sqlite` | Sold comps cache |
| `COMPS_TTL_DAYS` | `7` | Cache reuse period |
| `AI_MAX_IDENTIFICATIONS` | `12` | Cap on AI identifications per hunt |

Other catalog, identity and marketplace settings remain in `src/bazar_deals/data/bazar.yaml`.

GitHub Actions uses Copilot CLI with `COPILOT_MODEL=auto`, which is compatible with Copilot Free/Student. Paid Copilot seats can override this with a specifically available model.

## Alerts

Only **BUY** deals that pass the >=30 EUR expected-net-profit gate are posted to the GitHub Deal alerts collector issue. The alert includes purchase price, conservative quick-sale value, shipping, fees, condition haircut, risk reserve and expected clean profit.

## Selling own stock

`hunt` finds things to buy. `sell` does the opposite: it takes the stock already
listed on the four seller accounts and works out where the buyers are.

```powershell
python -m bazar_deals sell
python -m bazar_deals sell --refresh
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
