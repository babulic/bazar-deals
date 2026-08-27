from decimal import Decimal

import pytest

from bazar_deals.cli import main
from bazar_deals.selling.channels import channel, channels, reach_matrix, uncovered_countries
from bazar_deals.selling.inventory import (
    DEFAULT_WEIGHT_G,
    PACKAGED_INVENTORY,
    InventoryItem,
    load_inventory,
)
from bazar_deals.selling.packeta import PacketaRates
from bazar_deals.selling.plan import build_plan
from bazar_deals.selling.report import format_json, format_markdown
from bazar_deals.selling.titles import (
    TitlePart,
    build_title,
    fit_parts,
    localize,
    localize_locality,
    truncate_on_word_boundary,
)

RATES = {
    "max_weight_kg": 10,
    "fuel_surcharge_rate": 0.10,
    "toll_eur_per_kg": 0.04,
    "destinations": {
        "SK": {"pickup": 2.00, "home": 3.00, "schengen": True},
        "AT": {"pickup": 4.00, "home": 6.00, "schengen": True},
    },
}


def mineral(**overrides) -> InventoryItem:
    payload = {
        "id": "test-kalcit",
        "segment": "minerals",
        "title": "100g kalcit kryštály zo Štiavnice",
        "species": ["kalcit"],
        "form": "kryštál",
        "origin": "slovensko",
        "locality": "banská štiavnica",
        "weight_g": 100,
        "listed": {"bazos": Decimal("9")},
    }
    payload.update(overrides)
    return InventoryItem(**payload)


def test_packeta_adds_fuel_and_per_started_kilogram_toll() -> None:
    quote = PacketaRates(RATES).quote("AT", weight_g=100)
    assert quote.base_eur == Decimal("4.00")
    assert quote.fuel_eur == Decimal("0.40")
    # 100 g still counts as one started kilogram.
    assert quote.toll_eur == Decimal("0.04")
    assert quote.total_eur == Decimal("4.44")


def test_packeta_toll_scales_with_started_kilograms() -> None:
    rates = PacketaRates(RATES)
    assert rates.quote("AT", weight_g=1001).toll_eur == Decimal("0.08")
    assert rates.quote("AT", weight_g=2000).toll_eur == Decimal("0.08")


def test_packeta_rejects_unknown_country_and_overweight() -> None:
    rates = PacketaRates(RATES)
    with pytest.raises(KeyError):
        rates.quote("US")
    with pytest.raises(ValueError):
        rates.quote("AT", weight_g=20_000)
    assert rates.cheapest(["AT", "SK", "US"]).country == "SK"


def test_truncate_never_leaves_a_half_word() -> None:
    assert truncate_on_word_boundary("Amethyst Kristall Namibia", 14) == "Amethyst"
    assert truncate_on_word_boundary("Amethyst", 4) == "Amet"
    assert truncate_on_word_boundary("short", 40) == "short"


def test_fit_parts_drops_least_important_first() -> None:
    parts = [
        TitlePart(text="Amethyst", priority=1),
        TitlePart(text="Schemnitz", priority=2),
        TitlePart(text="Sammlerstück", priority=8),
    ]
    assert fit_parts(parts, 80) == "Amethyst, Schemnitz, Sammlerstück"
    assert fit_parts(parts, 25) == "Amethyst, Schemnitz"
    # A priority-1 fragment is kept even when it must be cut.
    assert fit_parts(parts, 5) == "Ameth"


def test_german_title_leads_with_the_historic_locality_name() -> None:
    title = build_title(mineral(), language="de", limit=80)
    assert title.startswith("Calcit Kristall 100g")
    assert "Schemnitz" in title
    # The modern name follows only because the budget allows it.
    assert "Banská Štiavnica" in title
    assert len(title) <= 80


def test_bazos_budget_drops_the_optional_fragments() -> None:
    item = mineral()
    wide = build_title(item, language="de", limit=80)
    narrow = build_title(item, language="de", limit=40)
    assert len(narrow) <= 40
    assert narrow.startswith("Calcit Kristall 100g")
    assert len(narrow) < len(wide)
    assert "Sammlerstück" not in narrow


def test_locality_transliteration_is_not_repeated() -> None:
    english, slovak = localize_locality("banská štiavnica", "en")
    assert english == "Banska Stiavnica"
    assert slovak == "Banská Štiavnica"
    title = build_title(mineral(), language="en", limit=80)
    assert title.count("tiavnica") == 1


def test_retro_title_leads_with_the_part_number() -> None:
    item = InventoryItem(
        id="vic",
        segment="retro",
        title="Videochip CSG 8565 R2 VIC-II pre Commodore C64C",
        part_numbers=["8565R2", "VIC-II"],
        listed={"ebay": Decimal("33")},
    )
    assert build_title(item, language="de", limit=80).startswith("8565R2 VIC-II Videochip")
    assert build_title(item, language="pl", limit=80).startswith("8565R2 VIC-II Układ wideo")


def test_commodity_title_falls_back_to_the_original_prose() -> None:
    item = InventoryItem(
        id="cable",
        segment="commodity",
        title="Samsung EP-DG925U 1.2m biely dátový a nabíjací microUSB kábel",
        listed={"bazos": Decimal("4")},
    )
    assert build_title(item, language="sk", limit=60) == (
        "Samsung EP-DG925U 1.2m biely dátový a nabíjací microUSB"
    )


def test_glossary_falls_back_to_the_key_for_unknown_terms() -> None:
    assert localize("ametyst", "hu") == "Ametiszt"
    assert localize("nonexistent-species", "de") == "nonexistent-species"


def test_channels_separate_reach_from_hosting_country() -> None:
    vinted = channel("vinted_sk")
    assert vinted.country == "SK"
    # Confirmed corridors for a Slovak Vinted seller are CZ and PL only.
    assert vinted.reach == ["SK", "CZ", "PL"]
    assert not vinted.reaches("DE")
    assert channel("willhaben").status == "rejected"
    assert not channel("willhaben").is_open()


def test_rejected_channels_stay_out_of_the_reach_matrix() -> None:
    matrix = reach_matrix()
    assert "willhaben" not in {cid for ids in matrix.values() for cid in ids}
    assert "ebay_at" in matrix["AT"]
    assert matrix["SK"][0] in {"aukro_sk", "bazos_sk", "vinted_sk"}


def test_hungary_has_no_live_channel_today() -> None:
    assert uncovered_countries(["SK", "CZ", "PL", "HU", "AT"]) == ["HU"]


def test_channel_ids_and_inventory_keys_are_unique() -> None:
    ids = [entry.id for entry in channels()]
    assert len(ids) == len(set(ids))
    live_keys = [entry.inventory_key() for entry in channels() if entry.status == "active"]
    assert sorted(live_keys) == ["aukro", "bazos", "ebay", "vinted"]


def test_inventory_snapshot_loads_with_prices_per_marketplace() -> None:
    stock = load_inventory(PACKAGED_INVENTORY)
    assert stock.collected == "2026-08-27"
    # Only Vinted still hides items behind infinite scroll.
    assert stock.partial == ["vinted"]
    assert stock.segments() == ["commodity", "minerals", "retro"]
    assert stock.coverage() == {"aukro": 27, "bazos": 19, "ebay": 19, "vinted": 20}
    item = stock.get("amethyst-namibia-74mm")
    assert item.price() == Decimal("115")
    assert item.home_price() == Decimal("110")
    assert item.missing_from({"bazos", "aukro", "vinted", "ebay"}) == []
    assert stock.get("galenit-terezia").missing_from({"bazos", "ebay"}) == ["ebay"]


def test_missing_weight_uses_the_default_tier() -> None:
    item = load_inventory(PACKAGED_INVENTORY).get("amethyst-namibia-74mm")
    assert not item.weight_is_known()
    assert item.shipping_weight_g() == DEFAULT_WEIGHT_G


def test_plan_flags_ebay_postage_above_the_real_packeta_cost() -> None:
    plan = build_plan(load_inventory(PACKAGED_INVENTORY))
    amethyst = next(entry for entry in plan.items if entry.item.id == "amethyst-namibia-74mm")
    assert amethyst.overcharge_eur > Decimal("5")
    assert any("postage is suppressing the sale" in note for note in amethyst.notes)
    assert plan.total_overcharge_eur() > Decimal("100")


def test_plan_blocks_export_when_postage_outweighs_the_price() -> None:
    plan = build_plan(load_inventory(PACKAGED_INVENTORY))
    chalcedony = next(entry for entry in plan.items if entry.item.id == "chalcedon-kremenisko")
    # A 7 EUR specimen cannot carry cross-border postage on its own.
    assert chalcedony.viable_countries() == ["SK"]
    assert "AT" in chalcedony.blocked_countries()

    amethyst = next(entry for entry in plan.items if entry.item.id == "amethyst-namibia-74mm")
    assert "AT" in amethyst.viable_countries()


def test_plan_reports_channel_gaps_and_respects_title_limits() -> None:
    plan = build_plan(load_inventory(PACKAGED_INVENTORY))
    gaps = plan.gaps_by_channel()
    assert gaps["allegro"] > 0
    assert "willhaben" not in gaps
    for item_plan in plan.items:
        for channel_plan in item_plan.channels:
            assert channel_plan.title
            assert len(channel_plan.title) <= channel_plan.title_limit


def test_plan_marks_vinted_only_stock() -> None:
    from bazar_deals.selling.inventory import Inventory

    only_vinted = Inventory(
        items=[
            InventoryItem(
                id="pseudomalachit",
                segment="minerals",
                title="Agregát pseudomalachitu v kremeni",
                species=["pseudomalachit"],
                locality="ľubietová",
                origin="slovensko",
                listed={"vinted": Decimal("29")},
            )
        ]
    )
    entry = build_plan(only_vinted).items[0]
    assert any("Vinted only" in note for note in entry.notes)
    assert any("Libethen" in note for note in entry.notes)


def test_reports_render_in_both_formats() -> None:
    plan = build_plan(load_inventory(PACKAGED_INVENTORY))
    markdown = format_markdown(plan, segment="retro")
    assert "## Segment: retro" in markdown
    assert "## Segment: minerals" not in markdown
    assert "8565R2" in markdown

    payload = format_json(plan, segment="minerals")
    assert '"segment": "minerals"' in payload
    assert '"segment": "retro"' not in payload


def test_sell_command_runs_without_network(capsys) -> None:
    assert main(["sell", "--segment", "minerals"]) == 0
    assert "Amethyst Kristall" in capsys.readouterr().out

    assert main(["sell", "--format", "json"]) == 0
    assert '"channel_id"' in capsys.readouterr().out


def test_plan_names_the_exact_shortfall_against_the_account() -> None:
    plan = build_plan(load_inventory(PACKAGED_INVENTORY))
    # The Vinted profile advertises 29 items; infinite scroll hides the rest.
    assert plan.shortfall() == {"vinted": (20, 29)}
    assert "9 are unaccounted for" in format_markdown(plan, segment="minerals")


def test_no_shortfall_is_reported_for_complete_accounts() -> None:
    plan = build_plan(load_inventory(PACKAGED_INVENTORY))
    for marketplace in ("bazos", "aukro", "ebay"):
        assert marketplace not in plan.shortfall()
