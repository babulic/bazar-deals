from bazar_deals.github_alerts import ALERT_LABEL
from bazar_deals.rules import rules


def test_yaml_holds_lists_and_gates() -> None:
    data = rules()
    assert data["github"]["alert_label"] == "bazar-alert"
    assert ALERT_LABEL == "bazar-alert"
    assert "gauč" in data["catalog"]["bulky_keywords"]
    assert "kazeta" in data["identity"]["kind_markers"]["media"]
    assert data["domain"]["item_kinds"][-1] == "generic"
    assert data["hunt"]["max_buy_eur"] == 60
    assert data["hunt"]["max_price_vs_typical"] == 0.5
    assert data["hunt"]["min_sold_sample"] == 5
    assert "phones" in data["domain"]["item_kinds"]
    assert "clothing" in data["domain"]["item_kinds"]
    assert "minerals" in data["domain"]["item_kinds"]
    assert data["identity"]["kind_priority"].index("jewelry") < data["identity"]["kind_priority"].index("minerals")
    assert data["hunt"]["max_shipping_eur"] == 15
    assert data["hunt"]["cheap_buy_eur"] == 20
    assert data["hunt"]["max_shipping_cheap_eur"] == 11
    assert data["hunt"]["comps_db"] == ".cache/bazar-comps.sqlite"
    assert data["hunt"]["comps_ttl_days"] == 7
    assert "3213" in data["ebay"]["small_categories"]
    assert "os" in data["catalog"]["small_bazos_rubs"]
    assert "du" in data["catalog"]["small_bazos_rubs"]
