"""Public search-engine index of classifieds when the site itself is WAF/login blocked.

This is not a login bypass: DuckDuckGo HTML is a public index. Only already-public
Facebook Marketplace item URLs and OLX /oferta/ URLs are kept. Login pages, groups,
category hubs and DDG ad redirects are dropped.
"""
from __future__ import annotations

import html as html_lib
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from bazar_deals.domain import Listing, Marketplace, Money

DDG_HTML = "https://html.duckduckgo.com/html/"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")
_A_TAG = re.compile(
    r"<a(?P<attrs>[^>]*class=\"result__a\"[^>]*)>(?P<title>.*?)</a>",
    re.I | re.S,
)
_SNIPPET_TAG = re.compile(
    r"<a(?P<attrs>[^>]*class=\"result__snippet\"[^>]*)>(?P<snippet>.*?)</a>",
    re.I | re.S,
)
_HREF_RE = re.compile(r'\bhref="([^"]+)"', re.I)
_FB_ITEM_RE = re.compile(
    r"^https://www\.facebook\.com/marketplace/item/(\d+)/?$",
    re.I,
)
_OLX_ITEM_RE = re.compile(
    r"^https://www\.olx\.pl/(?:d/)?oferta/([^/?#]+)",
    re.I,
)


def parse_ddg_html(body: str) -> list[tuple[str, str, str]]:
    """Return unique (title, url, snippet) hits from DuckDuckGo's HTML endpoint."""
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    text = body or ""
    for match in _A_TAG.finditer(text):
        url = _uddg_url(_attr_href(match.group("attrs")))
        title = _plain(match.group("title"))
        if not url or not title or url in seen:
            continue
        if "duckduckgo.com/y.js" in url or "duckduckgo.com/l/" in url:
            continue
        snippet = ""
        rest = text[match.end() : match.end() + 2500]
        snip = _SNIPPET_TAG.search(rest)
        nxt = _A_TAG.search(rest)
        if snip and (nxt is None or snip.start() < nxt.start()):
            snippet = _plain(snip.group("snippet"))
        seen.add(url)
        found.append((title, url, snippet))
    return found


def search_ddg(
    query: str,
    *,
    region: str = "pl-pl",
    client: httpx.Client | None = None,
) -> list[tuple[str, str, str]]:
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html",
        "Accept-Language": "pl-PL,sk-SK,cs-CZ,en;q=0.8",
    }
    params = {"q": query, "kl": region}
    if client is not None:
        response = client.get(DDG_HTML, headers=headers, params=params)
    else:
        response = httpx.get(
            DDG_HTML, headers=headers, params=params, timeout=20.0, follow_redirects=True
        )
    response.raise_for_status()
    return parse_ddg_html(response.text)


def listings_from_index(
    source: str,
    query: str,
    hits: list[tuple[str, str, str]],
) -> list[Listing]:
    found: list[Listing] = []
    seen: set[str] = set()
    for title, url, snippet in hits:
        listing = _to_listing(source, title, url, snippet, query)
        if listing is None or listing.external_id in seen:
            continue
        seen.add(listing.external_id)
        found.append(listing)
    return found


def indexed_query(source: str, query: str) -> str:
    if source == "facebook":
        return f"site:facebook.com/marketplace/item {query}"
    if source == "olx":
        return f"site:olx.pl/d/oferta {query}"
    return f"site:{source} {query}"


def _to_listing(source: str, title: str, url: str, snippet: str, query: str) -> Listing | None:
    href = _canonical_item_url(url)
    if source == "facebook":
        match = _FB_ITEM_RE.match(href)
        if not match:
            return None
        identifier = match.group(1)
        currency = "EUR"
        href = f"https://www.facebook.com/marketplace/item/{identifier}/"
    elif source == "olx":
        match = _OLX_ITEM_RE.match(href)
        if not match:
            return None
        identifier = match.group(1).removesuffix(".html")
        currency = "PLN"
        href = href.split("?", 1)[0]
    else:
        return None
    return Listing(
        marketplace=Marketplace(source),
        external_id=identifier,
        title=title,
        description=snippet,
        url=href,
        price=Money(amount=0, currency=currency),
        search_query=query,
        raw={"indexed": True, "snippet": snippet},
    )


def _canonical_item_url(url: str) -> str:
    raw = (url or "").strip().split("#", 1)[0].split("?", 1)[0]
    if raw.startswith("http://"):
        raw = "https://" + raw[7:]
    raw = re.sub(r"^https://m\.facebook\.com", "https://www.facebook.com", raw, flags=re.I)
    raw = re.sub(r"^https://facebook\.com", "https://www.facebook.com", raw, flags=re.I)
    raw = re.sub(r"^https://olx\.pl", "https://www.olx.pl", raw, flags=re.I)
    return raw.rstrip("/")


def _attr_href(attrs: str) -> str:
    match = _HREF_RE.search(attrs or "")
    return match.group(1) if match else ""


def _uddg_url(href: str) -> str:
    raw = href.replace("&amp;", "&")
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    query = parse_qs(parsed.query)
    if "uddg" in query:
        return unquote(query["uddg"][0])
    host = (parsed.hostname or "").casefold()
    if parsed.scheme in {"http", "https"} and host and "duckduckgo.com" not in host:
        return raw
    return ""


def _plain(value: str) -> str:
    return " ".join(_TAG_RE.sub(" ", html_lib.unescape(value or "")).split())
