import pytest

from bazar_deals.adapters.vinted import VintedProClient, sign_vinted_request
from bazar_deals.domain import Vertical


def test_sign_header_follows_vinted_spec() -> None:
    body = '{ "event_types": ["CREATE_ITEM_SUCCESS"], "url": "https://example.com" }'
    header = sign_vinted_request(
        method="POST",
        path="/api/v1/webhooks",
        access_key="foo",
        signing_key="bar",
        body=body,
        timestamp=1704067200,
    )
    prefix, digest = header.split(",v1=")
    assert prefix == "t=1704067200"
    assert len(digest) == 64
    other = sign_vinted_request(
        method="POST",
        path="/api/v1/webhooks",
        access_key="foo",
        signing_key="bar",
        body="",
        timestamp=1704067200,
    )
    assert other != header


def test_catalog_hunt_is_refused() -> None:
    with pytest.raises(RuntimeError, match="sell-side"):
        VintedProClient().fetch_new(Vertical.APPLE)
