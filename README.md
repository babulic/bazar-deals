# bazar-deals

Marketplace mispricing hunter. The program does not guess market direction. It looks for a thing listed at 40 € that can be resold at 100 €.

```
new listing
     ↓
identify item
     ↓
normalize model
     ↓
estimated resale value
     ↓
shipping / fees / condition / seller risk
     ↓
NET PROFIT  →  BUY | ALERT | SKIP
```

## Example

```
Commodore 1541-II
Bazoš: 38 €
odhad resale: 89 €
shipping + fees: 18 €
estimated profit: 33 €  🔥 BUY
```

## Sources (deliberate constraints)

| Marketplace | Role | Integration |
|---|---|---|
| **eBay** | hunt + affiliate + later offers | Official Browse API (search, `newlyListed`, `itemAffiliateWebUrl` for EPN). Feed/Notification and Offer API only after eBay approves the app. |
| **Aukro** | monitor later + **automated selling** | Official Public REST API is sell-side. It does not provide bid/buy automation. |
| **Bazoš** | monitor public ads, **buy manually** | Official public RSS only (`rss.php`). No unofficial GitHub “private API”, no IP-block bypass. |
| **Vinted** | **automated selling** + manual buy | Official Vinted Pro Integrations (allowlisted, HMAC). Own items / orders / webhooks only. No public catalog API; no undocumented `/api/v2` or DataDome bypass. |

Bazoš publishes on the order of tens of thousands of new ads per day. RSS is partial compared to a full listing page, which is an acceptable trade for a production-safe source.

## Second monetization

Not every deal is bought. Personal alerts go to a single GitHub issue as **comments** (GitHub emails you; the latest comment is the latest deal). Later, ranked leftovers can still feed paid Telegram channels:

- RETRO DEALS SK/CZ
- MINERAL DEALS
- APPLE DEALS
- NETWORK HARDWARE DEALS

eBay deals can also use Partner Network affiliate URLs so some edges pay without inventory.

## Current MVP

Runnable locally without API keys:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m bazar_deals hunt --offline --source bazos
```

Live Bazoš RSS (polite delay between requests):

```powershell
python -m bazar_deals hunt --notify
```

Default hunt is **small shippable goods** on **ebay.de, vinted.sk, aukro.sk, bazos.sk** only. Auctions are skipped; only buy-now / listed-price ads.

Hourly GitHub Action plus a run on every push to `main`. `--notify` posts BUY/WATCH as comments on [Deal alerts #1](https://github.com/babulic/bazar-deals/issues/1).

Copy `.env.example` to `.env` before wiring eBay / Aukro / Vinted / GitHub / Telegram / LLM keys.

What is in code today:

- Domain model and net-profit scoring (Vinted Buyer Protection is buy-side: 5% + 0.70 €)
- Bazoš public RSS adapter + vertical keyword filters
- Seed catalog comps (expand with sold history later)
- eBay Browse client skeleton (OAuth client-credentials + affiliate header)
- Aukro sell client stub
- Vinted Pro HMAC client for own inventory (catalog hunt refused)
- CLI hunt output in the deal format above
- GitHub issue comments as the alert channel (email via GitHub notifications)

Not in this slice: Offer API bidding, live sold-comp valuation, Telegram posting, LLM identification, persistence.
