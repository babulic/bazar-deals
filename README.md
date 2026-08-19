# bazar-deals

Hunter for **small shippable goods listed at ≤ 60 €** (buy-now) on ebay.de, vinted.sk, aukro.sk, bazos.sk. It is not a retro/PC catalog and it does not invent a resale number.

```
newest buy-now ads ≤ 60 €
     ↓
drop bulky / auctions / weak identity
     ↓
tight query from the title (not “contains commodore 64”)
     ↓
median of similar *sold* ebay.de listings
     ↓
shipping / fees / condition
     ↓
NET PROFIT  →  BUY | WATCH | skip
```

## How typical price is computed

**Source:** public eBay.de sold/completed search (`LH_Sold=1&LH_Complete=1`). That is realised used-goods prices, not live asking prices and not a hardcoded table.

Each hunt run:

1. Pull newest buy-now ads under 60 € (Bazoš small-category RSS; eBay newest BIN; Aukro/Vinted newest buy-now).
2. Skip furniture/appliances/cars/bikes and auctions.
3. Build a **tight sold query** from distinctive title tokens. A Konami cassette that merely mentions C64 is **media**, not a C64 computer — it will not be priced as a 120 € PC.
4. Fetch sold ebay.de hits for that query (in-memory cache per run). Keep only sold titles that are the **same kind** and similar tokens.
5. Typical price = **median of those sold prices**, labeled `medián predaných na ebay.de (n=12)`.
6. If identity is weak or `n < 5` → no alert. Never BUY on a seed like “C64 = 120 €”.

Nothing is stored on disk. Heureka/Idealo/Keepa/Terapeak are **not** used unless you later set a paid key. `KEEPA_API_KEY` is reserved; it does not change behaviour today because Keepa needs ASINs we do not have.

eBay may 403 sold HTML from GitHub datacenter IPs or redirect sold search to sign-in. Then that listing is skipped — we do not fall back to fake numbers.

## Alerts

One digest comment per hunt on [Deal alerts #1](https://github.com/babulic/bazar-deals/issues/1), label **`bazar-alert`**, assigned to `babulic`, first line `@babulic` (`github-actions[bot]` + `github.token`), same pattern as polymarket-tracker / peer-order-finder.

## Run

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m bazar_deals hunt --offline --source bazos
python -m bazar_deals hunt --notify
```

Hourly GitHub Action plus push to `main`.
