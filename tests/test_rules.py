from decimal import Decimal
from pathlib import Path

from bazar_deals.config import Settings
from bazar_deals.github_alerts import ALERT_LABEL, ALERT_TOP_N
from bazar_deals.rules import _PACKAGE_YAML, rules


def test_yaml_holds_lists_and_gates() -> None:
    data = rules()
    assert data["github"]["alert_label"] == "bazar-alert"
    assert data["github"]["alert_top_n"] == ALERT_TOP_N
    assert data["github"]["sell_alert_label"] == "bazar-sell"
    assert data["github"]["sell_alert_issue_title"] == "Sell buyers"
    assert ALERT_LABEL == "bazar-alert"
    assert "gauč" in data["catalog"]["bulky_keywords"]
    assert "kazeta" in data["identity"]["kind_markers"]["media"]
    assert "videothek" in data["identity"]["kind_markers"]["media"]
    assert "kassette" in data["identity"]["kind_markers"]["media"]
    assert "pasek" in data["identity"]["kind_markers"]["accessories"]
    assert "alpine loop" not in data["identity"]["kind_markers"]["accessories"]
    assert "airpods" in data["identity"]["kind_markers"]["hardware"]
    assert data["domain"]["item_kinds"][-1] == "generic"
    hunt = data["hunt"]
    fees = data["fees"]
    ai = data["ai"]
    settings = Settings()
    dec = lambda mapping, key: Decimal(str(mapping[key]))
    assert settings.max_buy_eur == dec(hunt, "max_buy_eur")
    assert settings.min_buy_eur == dec(hunt, "min_buy_eur")
    assert settings.min_net_profit_eur == dec(hunt, "min_net_profit_eur")
    assert settings.alert_min_net_profit_eur == dec(hunt, "alert_min_net_profit_eur")
    assert settings.hunt_notify_progress is False
    assert settings.hunt_digest_timezone == "Europe/Bratislava"
    assert data["github"]["notify_progress"] is False
    assert data["github"]["digest_timezone"] == "Europe/Bratislava"
    assert settings.max_price_vs_typical == dec(hunt, "max_price_vs_typical")
    assert settings.alert_price_vs_typical == dec(hunt, "alert_price_vs_typical")
    assert settings.max_shipping_eur == dec(hunt, "max_shipping_eur")
    assert settings.cheap_buy_eur == dec(hunt, "cheap_buy_eur")
    assert settings.max_shipping_cheap_eur == dec(hunt, "max_shipping_cheap_eur")
    assert settings.default_shipping_eur == dec(hunt, "default_shipping_eur")
    assert settings.min_margin == dec(hunt, "min_margin")
    assert settings.comps_ttl_days == hunt["comps_ttl_days"]
    assert settings.comps_db == hunt["comps_db"]
    assert settings.fx_cache == hunt["fx_cache"]
    assert settings.hunt_batch_db == hunt["hunt_batch_db"]
    assert settings.fx_max_age_days == hunt["fx_max_age_days"]
    assert settings.comps_live_queries == hunt["max_sold_lookups"]
    assert settings.hunt_batch_page_size == hunt["batch_page_size"]
    assert settings.min_sold_sample == hunt["min_sold_sample"]
    assert settings.p25_factor == dec(hunt, "p25_factor")
    assert settings.comps_price_multiple == dec(hunt, "comps_price_multiple")
    assert settings.live_search_seconds == hunt["live_search_seconds"]
    assert settings.http_timeout_seconds == hunt["http_timeout_seconds"]
    assert settings.resale_fee_rate == dec(fees, "resale_fee_rate")
    assert settings.seller_risk_reserve_rate == dec(fees, "seller_risk_reserve_rate")
    assert settings.fx_fee_rate == dec(fees, "fx_fee_rate")
    assert settings.no_box_haircut_eur == dec(hunt, "no_box_haircut_eur")
    assert settings.battery_under_80_haircut_rate == dec(hunt, "battery_under_80_haircut_rate")
    assert settings.battery_80_84_haircut_rate == dec(hunt, "battery_80_84_haircut_rate")
    assert settings.battery_85_89_haircut_rate == dec(hunt, "battery_85_89_haircut_rate")
    assert settings.ai_provider == ai["provider"]
    assert settings.ai_max_reviews == ai["max_reviews"]
    assert settings.ai_max_identifications == ai["max_identifications"]
    assert settings.ai_review_ttl_days == ai["review_ttl_days"]
    assert settings.ai_min_confidence == ai["min_confidence"]
    assert settings.ai_timeout_seconds == ai["timeout_seconds"]
    assert settings.openai_base_url == ai["openai_base_url"]
    assert settings.openai_model == ai["openai_model"]
    assert settings.copilot_model == ai["copilot_model"]
    assert hunt["max_score_listings"] == 80
    assert hunt["min_battery_health_percent"] == settings.min_battery_health_percent
    assert hunt["battery_under_pct"] < hunt["battery_mid_pct"] < hunt["battery_good_pct"]
    assert hunt["live_search_workers"] == 4
    assert hunt["enrich_description_min_chars"] == 40
    assert hunt["insufficient_detail_min_chars"] == 10
    assert hunt["marketplace_priority"][0] == "vinted"
    assert hunt["high_risk_detail_kinds"] == ["phones", "hardware", "photo"]
    assert "bez krabičky" in hunt["no_box_markers"]
    assert ai["review_retries"] == 2
    assert "phones" in data["catalog"]["high_yield_kinds"]
    assert "media" in data["catalog"]["drop_kinds"]
    assert data["ebay"]["fetch_limit"] == 30
    assert data["ebay"]["comps_limit"] == 50
    assert data["aukro"]["search_size"] == 40
    assert data["vinted"]["search_limit"] == 48
    assert data["central_europe"]["expand_max_queries"] == 40
    assert "max_no_comp_alerts" not in hunt
    assert "iphone" in data["hunt"]["target_queries"]
    assert "pixel" in data["hunt"]["target_queries"]
    assert "airpods" in data["hunt"]["target_queries"]
    assert "commodore" in data["hunt"]["target_queries"]
    assert "commodore 1541" in data["hunt"]["fetch_queries"]
    assert "iphone se" in data["hunt"]["fetch_queries"]
    assert "c64" not in data["hunt"]["fetch_queries"]
    assert "kindle" in data["hunt"]["fetch_queries"]
    assert "iphone 14" in data["hunt"]["expand_queries"]
    assert data["central_europe"]["max_queries"] == 28
    assert "cz" in data["catalog"]["bazos_rss"]
    assert "phones" in data["domain"]["item_kinds"]
    assert "clothing" in data["domain"]["item_kinds"]
    assert "minerals" in data["domain"]["item_kinds"]
    assert data["identity"]["kind_priority"].index("jewelry") < data["identity"]["kind_priority"].index("minerals")
    assert "3213" in data["ebay"]["small_categories"]
    assert data["ebay"]["hunt_marketplace_ids"] == ["EBAY_DE", "EBAY_AT"]
    assert "os" in data["catalog"]["small_bazos_rubs"]
    assert "du" in data["catalog"]["small_bazos_rubs"]
    assert "mo" in data["catalog"]["small_bazos_rubs"]
    assert "ob" not in data["catalog"]["small_bazos_rubs"]
    assert "kn" not in data["catalog"]["small_bazos_rubs"]
    assert "sp" not in data["catalog"]["small_bazos_rubs"]
    assert "de" not in data["catalog"]["small_bazos_rubs"]
    assert "vánočn" in data["catalog"]["christmas_markers"]
    assert "světelný řetěz" in data["catalog"]["christmas_light_products"]
    assert "televízor" in data["catalog"]["bulky_keywords"]
    assert 100838 in data["aukro"]["small_categories"]
    assert 90713 in data["aukro"]["small_categories"]
    assert 148663 in data["aukro"]["small_categories"]
    assert 88874 in data["aukro"]["small_categories"]
    assert 8525 not in data["aukro"]["small_categories"]
    assert 52651 not in data["aukro"]["small_categories"]
    assert 144281 not in data["aukro"]["small_categories"]
    assert "139973" in data["ebay"]["small_categories"]
    assert "31387" in data["ebay"]["small_categories"]
    assert "11450" not in data["ebay"]["small_categories"]
    assert "16212" not in data["ebay"]["small_categories"]
    assert "183454" not in data["ebay"]["small_categories"]
    assert "19068" not in data["ebay"]["small_categories"]
    assert "16-footwear" not in data["vinted"]["catalogs"]
    assert "19-bags_backpacks" not in data["vinted"]["catalogs"]
    assert "4-womens" not in data["vinted"]["catalogs"]
    assert "3565-electronics_phones" in data["vinted"]["catalogs"]
    assert "3004-electronics_wearables" in data["vinted"]["catalogs"]
    assert "4874-hc_trading_cards" not in data["vinted"]["catalogs"]
    assert "tričko" in data["catalog"]["fashion_drop_markers"]
    assert "apple watch se" in data["hunt"]["fetch_queries"]
    assert "iphone 11" in data["hunt"]["fetch_queries"]
    markers = data["identity"]["kind_markers"]["minerals"]
    assert "topás" in markers
    assert "alexandrit" in markers
    assert "alexandrid" in markers
    assert "diamant" in markers
    assert "smaragd" in markers
    assert "tanzanit" in markers


def test_hunt_score_seconds_accepts_github_actions_5400(monkeypatch) -> None:
    assert Settings(hunt_score_seconds=5400).hunt_score_seconds == 5400
    monkeypatch.setenv("HUNT_SCORE_SECONDS", "5400")
    assert Settings().hunt_score_seconds == 5400
    monkeypatch.setenv("HUNT_SCORE_SECONDS", "99999")
    assert Settings().hunt_score_seconds == rules()["hunt"]["max_score_seconds"]


def test_catalog_is_only_the_packaged_yaml() -> None:
    root = Path(__file__).resolve().parents[1]
    assert _PACKAGE_YAML == root / "src" / "bazar_deals" / "data" / "config.yaml"
    assert not (root / "bazar.yaml").exists()
    assert not (root / "src" / "bazar_deals" / "data" / "bazar.yaml").exists()
    assert not (root / "src" / "bazar_deals" / "watchlist.py").exists()
    assert rules()["hunt"]["min_net_profit_eur"] == 20
    assert rules()["hunt"]["comps_db"] == ".cache/bazar-comps-v2.sqlite"


def test_scheduled_hunt_exports_ai_budget_from_yaml() -> None:
    hunt_yaml = Path(".github/workflows/hunt.yml").read_text(encoding="utf-8")
    assert "from bazar_deals.watchlist" not in hunt_yaml
    assert "from bazar_deals.rules import rules" in hunt_yaml
    assert "AI_MAX_REVIEWS={ai['max_reviews']}" in hunt_yaml
    assert "AI_MAX_IDENTIFICATIONS={ai['max_identifications']}" in hunt_yaml
    assert "MAX_SCORE_LISTINGS={hunt['max_score_listings']}" in hunt_yaml
