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
