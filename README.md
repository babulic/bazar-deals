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
small + working + shoebox-scale (max 5 kg) + purchase at least 20 EUR
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

## What is searched

The hunt looks for **small, working goods that fit a shoebox and weigh at most 5 kg**, priced **20–110 EUR**.

- **Bazoš** RSS rubrics: Počítače, Mobily, Elektro, Foto, Hudba, Oblečenie, Knihy, Ostatné, Dom a záhrada, Šport, Deti. Furniture, cars, motorcycles, machines, jobs, real estate, services, tickets and animals are not fetched.
- **Aukro** public newest buy-now window, then the same physical/price filters. Seasonal LED Christmas lighting and string lights are dropped as commodity dumps.
- **Vinted** public catalog newest in the purchase-price range.
- **eBay.de** Browse API small categories (clothing, books, toys, electronics, cameras, games, jewelry, collectibles, minerals, coins, stamps, beauty, musical, sporting, phones, computers) only when `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` are set, and only with confirmed delivery to Slovakia.

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

The sold-comps budget (`hunt.max_sold_lookups`, default 80) counts **unique
normalized product queries**, not listings. Ten ads for the same iPhone 13
128GB cost one eBay sold search. The previous cap of 40 counted every listing,
so a pile of duplicate phones exhausted the budget before the 41st distinct
product was ever priced.

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

The fail-closed **price** review (also Copilot Free with `COPILOT_MODEL=auto`)
receives the same whole-advertisement text plus the extracted spec profile. It
may only lower the deterministic P25 or veto the alert; it cannot raise a value
to make a deal pass.

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
| `MIN_BUY_EUR` | `20` | Minimum purchase price; cheaper ads have no profit room |
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

The hourly GitHub Actions hunt always comments on the Deal alerts collector
issue ([issue #1](https://github.com/babulic/bazar-deals/issues/1)). Cards are
**BUY only**, ranked by expected net profit, at most 5 per hunt. If nothing
clears the 30 EUR net-profit floor, the comment is status and funnel only —
losing items are not posted as fillers. The assignee is mentioned only when at
least one BUY card is present.

eBay listings require repo Actions secrets `EBAY_CLIENT_ID` and
`EBAY_CLIENT_SECRET`. Without them the hunt still runs Bazos/Aukro/Vinted, but
eBay is skipped.

## Run

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m bazar_deals hunt --offline --source bazos
python -m bazar_deals hunt --notify
```
