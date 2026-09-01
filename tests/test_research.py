from collections import Counter
from pathlib import Path

from bazar_deals.catalog import hunt_target_queries, matches_hunt_target
from bazar_deals.research import hunt_research_hint, sell_research_hint, write_github_output


def test_target_sku_titles_are_prioritized() -> None:
    assert matches_hunt_target("Apple iPhone 13 128GB")
    assert matches_hunt_target("Commodore 1541")
    assert not matches_hunt_target("Dámske tričko veľkosť M")


def test_expand_queries_join_only_in_research_mode(monkeypatch) -> None:
    monkeypatch.delenv("BAZAR_HUNT_RESEARCH", raising=False)
    monkeypatch.delenv("BAZAR_HUNT_EXPAND", raising=False)
    base = hunt_target_queries()
    assert "iphone" in base
    assert "kindle" not in base
    monkeypatch.setenv("BAZAR_HUNT_RESEARCH", "1")
    expanded = hunt_target_queries()
    assert "iphone" in expanded
    assert "kindle" in expanded
    assert "vltavín" in expanded


def test_zero_buy_hint_points_at_more_hits_not_tighter_gates() -> None:
    hint = hunt_research_hint(Counter(usable=80, no_sold_comps=71, buy=0))
    assert "expand" in hint.casefold() or "comps" in hint
    empty = hunt_research_hint(Counter(usable=0, buy=0))
    assert "widen" in empty
    assert "kúpim" in sell_research_hint(buyers=0, fetched=12)
    assert "boards" in sell_research_hint(buyers=0, fetched=0)


def test_github_output_writes_buys_and_buyers(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))
    write_github_output(buys=0, buyers=0, research=1)
    text = target.read_text(encoding="utf-8")
    assert "buys=0" in text
    assert "buyers=0" in text
    assert "research=1" in text
