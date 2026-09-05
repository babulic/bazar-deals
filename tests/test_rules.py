from bazar_deals.config import Settings
from bazar_deals.github_alerts import ALERT_LABEL
from bazar_deals.rules import rules


def test_yaml_holds_lists_and_gates() -> None:
    data = rules()
    assert data["github"]["alert_label"] == "bazar-alert"
    assert data["github"]["alert_top_n"] == 5
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
    assert data["hunt"]["max_buy_eur"] == 130
    assert data["hunt"]["min_buy_eur"] == 15
    assert data["hunt"]["max_weight_kg"] == 2
    assert data["hunt"]["max_edge_cm"] == 50
    assert data["hunt"]["max_sum_cm"] == 120
    assert data["hunt"]["min_net_profit_eur"] == 20
    assert data["hunt"]["max_price_vs_typical"] == 0.5
    assert data["hunt"]["alert_price_vs_typical"] == 1.0
    assert "max_no_comp_alerts" not in data["hunt"]
    assert data["hunt"]["min_sold_sample"] == 5
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
    assert Settings().comps_live_queries == 80
    assert data["hunt"]["max_score_listings"] == 80
    assert Settings().hunt_batch_page_size == 80
    assert Settings().copilot_model == "auto"
    assert "phones" in data["domain"]["item_kinds"]
    assert "clothing" in data["domain"]["item_kinds"]
    assert "minerals" in data["domain"]["item_kinds"]
    assert data["identity"]["kind_priority"].index("jewelry") < data["identity"]["kind_priority"].index("minerals")
    assert data["hunt"]["max_shipping_eur"] == 15
    assert data["hunt"]["cheap_buy_eur"] == 20
    assert data["hunt"]["max_shipping_cheap_eur"] == 11
    assert data["hunt"]["comps_db"] == ".cache/bazar-comps-v2.sqlite"
    assert data["hunt"]["comps_ttl_days"] == 7
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
    assert Settings().hunt_score_seconds == 7200
