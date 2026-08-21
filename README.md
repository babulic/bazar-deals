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
strict identity / variant matching
(storage, year, phone model, Pro/Max/Plus/Mini/Ultra etc.)
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

Other catalog, identity and marketplace settings remain in `src/bazar_deals/data/bazar.yaml`.

GitHub Actions uses Copilot CLI with `COPILOT_MODEL=auto`, which is compatible with Copilot Free/Student. Paid Copilot seats can override this with a specifically available model.

## Alerts

Only **BUY** deals that pass the >=30 EUR expected-net-profit gate are posted to the GitHub Deal alerts collector issue. The alert includes purchase price, conservative quick-sale value, shipping, fees, condition haircut, risk reserve and expected clean profit.

## Run

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m bazar_deals hunt --offline --source bazos
python -m bazar_deals hunt --notify
```
