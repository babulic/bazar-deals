# bazar-deals

Hunter for **small shippable, working goods**. Sites: ebay.de, vinted.sk, aukro.sk, bazos.sk. Buy-now only. Auctions are out.

```
newest buy-now ads
     ↓
price ≤ MAX_BUY_EUR (default 60)
     ↓
drop bulky, auctions, damaged / for-parts
     ↓
identify the listing (weak title → skip)
     ↓
typical price = median of similar *sold* ebay.de working items
     ↓
BUY only if price ≤ typical × MAX_PRICE_VS_TYPICAL (default 0.5)
```

## Decision (the only BUY rule)

Alert when **all** of this is true:

1. The listing is **buy-now**, **small**, **≤ max buy price**.
2. Text does not say damaged / for parts / not working. eBay `FOR_PARTS` is dropped.
3. Identity is tight enough to search sold comps (a Konami C64 *cassette* is not a C64 computer).
4. There are enough **sold** ebay.de peers of the same kind (`n ≥ 5`).
5. Listed price **≤ usual sold median × max_price_vs_typical**.

Default `MAX_PRICE_VS_TYPICAL=0.5` means listed price at most **half** the usual working-condition sold price. Set `1.0` for at-or-below typical. Set `MAX_BUY_EUR=40` to cap spend.

Usual price is **not** a hardcoded table and **not** live asking prices. It is the median of recent **sold** ebay.de listings that pass the same working-condition + similarity check. Hunt reads a local SQLite cache first (`COMPS_DB`, default `.cache/bazar-comps.sqlite`). eBay sold HTML is fetched only when that query is missing, older than `COMPS_TTL_DAYS` (default 7), or the stored sample is below `min_sold_sample`. If eBay HTML 403s from GitHub, hunt uses the last stored median when one exists — otherwise that listing is skipped. We do not invent a number.

GitHub Actions restores/saves `.cache/bazar-comps.sqlite` with `actions/cache@v4` (`sold-comps-v1-` prefix, `COMPS_DB=.cache/bazar-comps.sqlite`) so hourly hunts reuse sold comps across runs.

## Config

Lists, maps, and numeric defaults live in [`src/bazar_deals/data/bazar.yaml`](src/bazar_deals/data/bazar.yaml): bulky words, identity markers, damage phrases, Bazoš categories, hunt caps, fees, GitHub label. Optional overlay: `bazar.yaml` in the project root, or `BAZAR_CONFIG=/path/to.yaml`.

Secrets (tokens, API keys) stay in `.env`. Env still overrides hunt gates:

| Env | YAML key | Meaning |
|---|---|---|
| `MAX_BUY_EUR` | `hunt.max_buy_eur` | Max listed price to consider |
| `MAX_PRICE_VS_TYPICAL` | `hunt.max_price_vs_typical` | Buy if price ≤ typical × this |
| `MAX_SHIPPING_EUR` | `hunt.max_shipping_eur` | Assumed postage when listed price ≥ 20 EUR (default 15) |
| `CHEAP_BUY_EUR` | `hunt.cheap_buy_eur` | Listed-price cutoff for the cheaper postage cap (default 20) |
| `MAX_SHIPPING_CHEAP_EUR` | `hunt.max_shipping_cheap_eur` | Assumed postage when listed price < 20 EUR (default 11) |
| `COMPS_DB` | `hunt.comps_db` | SQLite path for sold comps (default `.cache/bazar-comps.sqlite`) |
| `COMPS_TTL_DAYS` | `hunt.comps_ttl_days` | Reuse cached median this many days (default 7) |

## Alerts

One digest comment per hunt on [Deal alerts #1](https://github.com/babulic/bazar-deals/issues/1), label **`bazar-alert`**, assigned to `babulic`, first line `@babulic`.

## Run

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m bazar_deals hunt --offline --source bazos
python -m bazar_deals hunt --notify
```
