from bazar_deals.domain import Vertical

# Official public RSS only. Never unofficial private Bazos APIs.
BAZOS_RSS = {
    "sk": "https://www.bazos.sk/rss.php",
}

# Small, shippable categories. No cars, realty, bulky household.
SMALL_BAZOS_RUBS = (
    {"rub": "pc"},
    {"rub": "mo"},
    {"rub": "el"},
    {"rub": "fo"},
    {"rub": "hu"},
    {"rub": "ob"},
    {"rub": "kn"},
)

# Category codes used by Bazos public RSS (?rub=&cat=).
VERTICAL_RSS = {
    Vertical.RETRO: (
        {"rub": "pc"},
        {"rub": "el"},
        {"rub": "hu"},
        {"rub": "fo"},
    ),
    Vertical.APPLE: (
        {"rub": "pc"},
        {"rub": "mo"},
    ),
    Vertical.NETWORK: ({"rub": "pc"},),
    Vertical.MINERAL: (
        {"rub": "os"},
        {"rub": "du"},
    ),
}

VERTICAL_KEYWORDS = {
    Vertical.RETRO: (
        "commodore",
        "amiga",
        "atari",
        "zx spectrum",
        "c64",
        "1541",
        "nintendo",
        "gameboy",
        "playstation 1",
        "ps1",
        "walkman",
        "reel to reel",
    ),
    Vertical.APPLE: (
        "macbook",
        "iphone",
        "ipad",
        "imac",
        "mac mini",
        "mac pro",
        "airpods",
        "apple watch",
    ),
    Vertical.NETWORK: (
        "mikrotik",
        "unifi",
        "ubiquiti",
        "cisco",
        "juniper",
        "aruba",
        "fortigate",
        "synology",
        "qnap",
        "sfp",
        "switch 48",
    ),
    Vertical.MINERAL: (
        "ametyst",
        "amethyst",
        "kristal",
        "krystal",
        "mineral",
        "achát",
        "achat",
        "fluorit",
        "pyrit",
        "malachit",
    ),
}

BULKY_KEYWORDS = (
    "gauč",
    "gauce",
    "pohovka",
    "sedačka",
    "sedacka",
    "kreslo",
    "postel",
    "posteľ",
    "matrac",
    "skriňa",
    "skrina",
    "práčka",
    "pracka",
    "chladnička",
    "chladnicka",
    "mrazák",
    "mrazak",
    "umývačka",
    "umyvacka",
    "sporák",
    "sporak",
    "kosačka",
    "kosacka",
    "bicykel",
    "elektrokolobežka",
    "kolobezka",
    "automobil",
    "osobné auto",
    "osobne auto",
    "karavan",
)

# Seed comps until sold-history APIs are wired. Amounts are EUR.
CATALOG_COMPS: dict[str, tuple[str, float]] = {
    "commodore 1541-ii": ("Commodore 1541-II", 89),
    "commodore 1541": ("Commodore 1541", 79),
    "commodore 64": ("Commodore 64", 120),
    "macbook air m1": ("MacBook Air M1", 420),
    "iphone 13": ("iPhone 13", 280),
    "mikrotik hex": ("MikroTik hEX", 55),
    "unifi 6 lite": ("Ubiquiti UniFi 6 Lite", 70),
}


SMALL_SEARCH_QUERIES = (
    "iphone",
    "macbook",
    "ipad",
    "airpods",
    "nintendo",
    "playstation",
    "commodore",
    "mikrotik",
    "unifi",
    "canon",
    "sony a7",
)


def is_bulky(text: str) -> bool:
    hay = text.casefold()
    return any(keyword in hay for keyword in BULKY_KEYWORDS)
