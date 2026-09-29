import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
from pydantic import SecretStr

from app.adapters.outbound.amocrm.errors import AmoCRMAuthError
from app.adapters.outbound.amocrm.tokens import TokenPair, TokenStore
from app.domain.errors import CRMUnavailable

logger = logging.getLogger(__name__)

_REAUTH_HINT = "run make amocrm-auth code=<authorization code from the integration card>"


class AmoCRMOAuth:
    """OAuth 2.0 AmoCRM: access-токен живёт сутки, refresh-токен одноразовый."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        subdomain: str,
        client_id: str,
        client_secret: SecretStr,
        redirect_uri: str,
        store: TokenStore,
        clock: Callable[[], float] = time.time,
        refresh_margin_s: float = 60.0,
    ) -> None:
        self._http = http
        self._token_url = f"https://{subdomain}.amocrm.ru/oauth2/access_token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._store = store
        self._clock = clock
        self._refresh_margin_s = refresh_margin_s
        self._cached: TokenPair | None = None
        self._lock = asyncio.Lock()

    async def exchange_code(self, code: str) -> None:
        pair = await self._request({"grant_type": "authorization_code", "code": code})
        await self._store.save(pair)
        self._cached = pair

    async def access_token(self) -> str:
        if self._cached is not None and self._is_fresh(self._cached):
            return self._cached.access_token
        async with self._lock:
            pair = self._cached or await self._store.load()
            if pair is None:
                raise AmoCRMAuthError(f"AmoCRM is not authorized; {_REAUTH_HINT}")
            # Пока ждали lock, токен мог обновить другой запрос
            if not self._is_fresh(pair):
                pair = await self._refresh(pair)
            self._cached = pair
            return pair.access_token

    def invalidate(self) -> None:
        if self._cached is not None:
            self._cached = replace(self._cached, expires_at=0.0)

    def _is_fresh(self, pair: TokenPair) -> bool:
        return self._clock() < pair.expires_at - self._refresh_margin_s

    async def _refresh(self, pair: TokenPair) -> TokenPair:
        new_pair = await self._request(
            {"grant_type": "refresh_token", "refresh_token": pair.refresh_token}
        )
        # Старый refresh-токен уже сгорел: новую пару сохраняем до того, как ей пользоваться
        await self._store.save(new_pair)
        logger.info("AmoCRM access token refreshed")
        return new_pair

    async def _request(self, grant: dict[str, str]) -> TokenPair:
        payload: dict[str, Any] = {
            "client_id": self._client_id,
            "client_secret": self._client_secret.get_secret_value(),
            "redirect_uri": self._redirect_uri,
            **grant,
        }
        try:
            response = await self._http.post(self._token_url, json=payload)
        except httpx.HTTPError as exc:
            raise CRMUnavailable("AmoCRM OAuth request failed") from exc
        if response.status_code in (400, 401):
            raise AmoCRMAuthError(f"AmoCRM rejected the OAuth grant; {_REAUTH_HINT}")
        if response.status_code != httpx.codes.OK:
            raise CRMUnavailable(f"AmoCRM OAuth failed with status {response.status_code}")
        data = response.json()
        return TokenPair(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=self._clock() + int(data["expires_in"]),
        )
