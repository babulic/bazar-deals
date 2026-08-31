"""Application OAuth for already-authorized Allegro listing integrations.

This does not register applications or grant access to /offers/listing.
Tokens live only in process memory; neither credentials nor responses are logged.
"""
from __future__ import annotations

import time

import httpx

from bazar_deals.config import Settings

USER_AGENT = "bazar-deals/0.1 (+https://github.com/babulic/bazar-deals)"
TOKEN_URL = "https://allegro.pl/auth/oauth/token"


class AllegroAuth:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client
        self._token = ""
        self._expires_at = 0.0

    @property
    def automatic(self) -> bool:
        return bool(self.settings.allegro_listing_access_confirmed
                    and self.settings.allegro_client_id.strip()
                    and self.settings.allegro_client_secret.strip())

    @property
    def configured(self) -> bool:
        return self.automatic or bool(self.settings.allegro_access_token.strip())

    def token(self, *, force: bool = False) -> str:
        if not self.automatic:
            token = self.settings.allegro_access_token.strip()
            if token:
                return token
            raise RuntimeError("ACCESS_NOT_GRANTED: an authorized Allegro application and credentials are required")
        if not force and self._token and time.monotonic() < self._expires_at:
            return self._token
        self._token = ""
        self._expires_at = 0.0
        started = time.monotonic()
        post = self.client.post if self.client else httpx.post
        try:
            response = post(TOKEN_URL, auth=httpx.BasicAuth(
                self.settings.allegro_client_id.strip(), self.settings.allegro_client_secret.strip()),
                data={"grant_type": "client_credentials"}, timeout=12, follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        except httpx.HTTPError:
            raise RuntimeError("LOGIN_REQUIRED: Allegro OAuth request failed; credentials and response omitted") from None
        if response.status_code != 200:
            raise RuntimeError(f"LOGIN_REQUIRED: Allegro OAuth HTTP {response.status_code}; verify application credentials")
        try:
            if len(response.content) > 65_536:
                raise ValueError("oversized response")
            payload = response.json()
            token = payload["access_token"]
            lifetime = payload["expires_in"]
            if (not isinstance(token, str) or not token or len(token) > 16_384
                    or not token.isascii() or any(c.isspace() or ord(c) < 32 for c in token)
                    or type(lifetime) is not int or lifetime <= 0
                    or str(payload.get("token_type", "")).lower() != "bearer"):
                raise ValueError("invalid token metadata")
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("LOGIN_REQUIRED: invalid Allegro OAuth response; response omitted") from None
        self._token = token
        self._expires_at = started + lifetime - min(60, lifetime / 10)
        return token
