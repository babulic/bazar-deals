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
    assert data["domain"]["item_kinds"][-1] == "generic"
    assert data["hunt"]["max_buy_eur"] == 110
    assert data["hunt"]["min_buy_eur"] == 20
    assert data["hunt"]["max_weight_kg"] == 5
    assert data["hunt"]["min_net_profit_eur"] == 30
    assert data["hunt"]["max_price_vs_typical"] == 0.5
    assert data["hunt"]["alert_price_vs_typical"] == 1.0
    assert "max_no_comp_alerts" not in data["hunt"]
    assert data["hunt"]["min_sold_sample"] == 5
    assert data["hunt"]["max_sold_lookups"] == 80
    assert Settings().comps_live_queries == 80
    assert data["hunt"]["max_score_listings"] == 80
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
    assert "os" in data["catalog"]["small_bazos_rubs"]
    assert "du" in data["catalog"]["small_bazos_rubs"]
    assert "sp" in data["catalog"]["small_bazos_rubs"]
    assert "de" in data["catalog"]["small_bazos_rubs"]
    assert "vánočn" in data["catalog"]["christmas_markers"]
    assert "světelný řetěz" in data["catalog"]["christmas_light_products"]
    assert "televízor" in data["catalog"]["bulky_keywords"]
    assert 100838 in data["aukro"]["small_categories"]
    assert 52651 in data["aukro"]["small_categories"]
    assert 144281 in data["aukro"]["small_categories"]
    assert 144304 in data["aukro"]["small_categories"]
    assert 148663 in data["aukro"]["small_categories"]
    assert 88874 in data["aukro"]["small_categories"]
    assert "183454" in data["ebay"]["small_categories"]
    assert "19068" in data["ebay"]["small_categories"]
    assert "16-footwear" in data["vinted"]["catalogs"]
    assert "3565-electronics_phones" in data["vinted"]["catalogs"]
    assert "4874-hc_trading_cards" in data["vinted"]["catalogs"]
    markers = data["identity"]["kind_markers"]["minerals"]
    assert "topás" in markers
    assert "alexandrit" in markers
    assert "alexandrid" in markers
    assert "diamant" in markers
    assert "smaragd" in markers
    assert "tanzanit" in markers
