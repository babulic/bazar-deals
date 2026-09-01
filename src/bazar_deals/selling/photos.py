"""Compare listing photos so a phone want-ad cannot pair with a watch strap."""
from __future__ import annotations

import io
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

_HASH_SIZE = 8
_MAX_BYTES = 2_000_000
# 8×8 aHash; same object is typically under 10, phone vs strap is far above 20.
_SAME_HAMMING = 14
# Grey / white / black photos are not a colour signal.
_GREY_CHROMA = 28.0


def average_hash(data: bytes, size: int = _HASH_SIZE) -> int | None:
    """64-bit average hash. None when the bytes are not an image Pillow can open."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        return None
    flatten = getattr(image, "get_flattened_data", image.getdata)
    pixels = list(flatten())
    if not pixels:
        return None
    mean = sum(pixels) / len(pixels)
    bits = 0
    for index, pixel in enumerate(pixels):
        if pixel >= mean:
            bits |= 1 << index
    return bits


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def photos_same_object(
    stock_urls: list[str],
    want_urls: list[str],
    *,
    fetch: Callable[[str], bytes | None] | None = None,
) -> bool | None:
    """True when any stock photo matches any want-ad photo.

    False when both sides had downloadable images and none were close.
    None when comparison could not run (no URLs, download failed, or no decoder).
    """
    stock = [url for url in stock_urls if _public_image_url(url)]
    want = [url for url in want_urls if _public_image_url(url)]
    if not stock or not want:
        return None
    getter = fetch or _download
    stock_hashes = [average_hash(blob) for blob in (_read(url, getter) for url in stock[:4])]
    want_hashes = [average_hash(blob) for blob in (_read(url, getter) for url in want[:4])]
    stock_hashes = [value for value in stock_hashes if value is not None]
    want_hashes = [value for value in want_hashes if value is not None]
    if not stock_hashes or not want_hashes:
        return None
    for left in stock_hashes:
        for right in want_hashes:
            if hamming_distance(left, right) <= _SAME_HAMMING:
                return True
    return False


def rgb_color_family(rgb: tuple[float, float, float]) -> str | None:
    """Map an average RGB triple to a coarse colour family, or None if grey."""
    red, green, blue = rgb
    chroma = max(red, green, blue) - min(red, green, blue)
    if chroma < _GREY_CHROMA:
        return None
    if green >= red + 12 and green >= blue + 12:
        return "green"
    if blue >= red + 15 and blue >= green + 10:
        return "blue"
    if red >= green and red >= blue:
        if green >= 90 and blue >= 80 and abs(green - blue) < 55:
            return "pink"
        if green >= 100 and blue < 80 and red - blue >= 40:
            return "yellow"
        if green >= 60 and blue < green * 0.75:
            return "red"
        return "red"
    if green >= blue and red >= 80:
        return "yellow"
    return None


def hex_color_family(value: str) -> str | None:
    """Parse `#rrggbb` / `#rgb` marketplace swatches (Vinted dominant_color)."""
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return None
    try:
        red = int(text[0:2], 16)
        green = int(text[2:4], 16)
        blue = int(text[4:6], 16)
    except ValueError:
        return None
    return rgb_color_family((float(red), float(green), float(blue)))


def mean_rgb(data: bytes) -> tuple[float, float, float] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image = image.resize((16, 16), Image.Resampling.BOX)
    except Exception:
        return None
    flatten = getattr(image, "get_flattened_data", image.getdata)
    raw = list(flatten())
    if not raw:
        return None
    if isinstance(raw[0], int):
        if len(raw) % 3:
            return None
        pixels = list(zip(raw[0::3], raw[1::3], raw[2::3], strict=False))
    else:
        pixels = raw
    count = len(pixels)
    red = sum(pixel[0] for pixel in pixels) / count
    green = sum(pixel[1] for pixel in pixels) / count
    blue = sum(pixel[2] for pixel in pixels) / count
    return (red, green, blue)


def photos_color_conflict(
    stock_urls: list[str],
    want_urls: list[str],
    *,
    fetch: Callable[[str], bytes | None] | None = None,
    stock_colors: set[str] | None = None,
    want_swatches: list[str] | None = None,
) -> bool | None:
    """True when stock and want photos (or swatches) name different colours.

    None when neither side has a usable colour signal. False when colours agree
    or only one side is known.
    """
    stock_families = set(stock_colors or ())
    want_families = {family for family in (hex_color_family(item) for item in want_swatches or []) if family}
    if not stock_families and not stock_urls:
        return None
    if not want_families and not want_urls:
        return None
    getter = fetch or _download
    if not stock_families:
        for url in stock_urls[:4]:
            if not _public_image_url(url):
                continue
            rgb = mean_rgb(_read(url, getter))
            family = rgb_color_family(rgb) if rgb else None
            if family:
                stock_families.add(family)
    if not want_families:
        for url in want_urls[:4]:
            if not _public_image_url(url):
                continue
            rgb = mean_rgb(_read(url, getter))
            family = rgb_color_family(rgb) if rgb else None
            if family:
                want_families.add(family)
    if not stock_families or not want_families:
        return None
    if stock_families & want_families:
        return False
    return True


def _read(url: str, getter: Callable[[str], bytes | None]) -> bytes:
    try:
        return getter(url) or b""
    except Exception:
        return b""


def _public_image_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and bool(host) and not parsed.username and parsed.port in (None, 443)


def _download(url: str) -> bytes | None:
    if not _public_image_url(url):
        return None
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    data = response.content or b""
    if not data or len(data) > _MAX_BYTES:
        return None
    return data
