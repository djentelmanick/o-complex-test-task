import asyncio
import logging
import time
import uuid
from collections.abc import Callable

import httpx
from pydantic import SecretStr

from app.domain.errors import LLMUnavailable

logger = logging.getLogger(__name__)


class GigaChatTokenProvider:
    """Access-токен GigaChat живёт 30 минут: кэшируем и обновляем заранее."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        auth_url: str,
        auth_key: SecretStr,
        scope: str,
        *,
        clock: Callable[[], float] = time.time,
        refresh_margin_s: float = 60.0,
    ) -> None:
        self._http = http
        self._auth_url = auth_url
        self._auth_key = auth_key
        self._scope = scope
        self._clock = clock
        self._refresh_margin_s = refresh_margin_s
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        if self._token is not None and self._is_fresh():
            return self._token
        async with self._lock:
            # Пока ждали lock, токен мог обновить другой запрос
            if self._token is None or not self._is_fresh():
                self._token = await self._fetch()
            return self._token

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0

    def _is_fresh(self) -> bool:
        return self._clock() < self._expires_at - self._refresh_margin_s

    async def _fetch(self) -> str:
        try:
            response = await self._http.post(
                self._auth_url,
                headers={
                    "Authorization": f"Basic {self._auth_key.get_secret_value()}",
                    "RqUID": str(uuid.uuid4()),
                    "Accept": "application/json",
                },
                data={"scope": self._scope},
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailable("GigaChat auth request failed") from exc
        if response.status_code != httpx.codes.OK:
            logger.warning("GigaChat auth failed with status %s", response.status_code)
            raise LLMUnavailable(f"GigaChat auth failed with status {response.status_code}")
        data = response.json()
        self._expires_at = data["expires_at"] / 1000
        return str(data["access_token"])
