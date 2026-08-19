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
