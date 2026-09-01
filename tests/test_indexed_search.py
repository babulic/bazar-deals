from urllib.parse import quote

from bazar_deals.adapters.indexed_search import (
    listings_from_index,
    parse_ddg_html,
)


def _ddg_html(*rows: tuple[str, str, str]) -> str:
    parts = ['<html>']
    for title, url, snippet in rows:
        href = f"//duckduckgo.com/l/?uddg={quote(url, safe='')}&amp;rut=x"
        parts.append(f'<a rel="nofollow" class="result__a" href="{href}">{title}</a>')
        parts.append(f'<a class="result__snippet" href="{href}">{snippet}</a>')
    parts.append("</html>")
    return "".join(parts)


def test_parse_ddg_html_unwraps_uddg_and_keeps_snippets() -> None:
    body = _ddg_html(
        (
            "Kupię MOS 6510",
            "https://www.olx.pl/d/oferta/kupie-mos-6510.html",
            "Szukam procesora",
        ),
        (
            "Kúpim ametyst",
            "https://www.facebook.com/marketplace/item/42/",
            "Hľadám kryštál",
        ),
    )
    hits = parse_ddg_html(body)
    assert hits[0][0] == "Kupię MOS 6510"
    assert hits[0][1] == "https://www.olx.pl/d/oferta/kupie-mos-6510.html"
    assert "Szukam" in hits[0][2]
    assert hits[1][1] == "https://www.facebook.com/marketplace/item/42/"


def test_listings_from_index_keep_item_urls_and_drop_noise() -> None:
    hits = parse_ddg_html(
        _ddg_html(
            (
                "Kupię 6510",
                "https://www.olx.pl/d/oferta/kupie-6510-CID1.html",
                "Szukam",
            ),
            (
                "OLX elektronika",
                "https://www.olx.pl/elektronika/",
                "kategoria",
            ),
            (
                "ad",
                "https://duckduckgo.com/y.js?ad=1",
                "sponsored",
            ),
        )
    )
    olx = listings_from_index("olx", "kupię 6510", hits)
    assert [item.external_id for item in olx] == ["kupie-6510-CID1"]
    assert olx[0].raw.get("indexed") is True

    facebook_hits = parse_ddg_html(
        _ddg_html(
            (
                "Kúpim 6510",
                "https://www.facebook.com/marketplace/item/99/",
                "WTB",
            ),
            (
                "Marketplace home",
                "https://www.facebook.com/marketplace/",
                "home",
            ),
            (
                "Group",
                "https://www.facebook.com/groups/123/",
                "group",
            ),
        )
    )
    facebook = listings_from_index("facebook", "kúpim 6510", facebook_hits)
    assert [item.external_id for item in facebook] == ["99"]
    assert str(facebook[0].url) == "https://www.facebook.com/marketplace/item/99/"
