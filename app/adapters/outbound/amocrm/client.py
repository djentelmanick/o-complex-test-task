import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import httpx

from app.adapters.outbound.amocrm.errors import AmoCRMNotFound
from app.domain.errors import CRMUnavailable

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

Params = Sequence[tuple[str, str]]


class AccessTokenSource(Protocol):
    async def access_token(self) -> str: ...

    def invalidate(self) -> None: ...


class AmoCRMClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        subdomain: str,
        tokens: AccessTokenSource,
        *,
        max_retries: int = 2,
        backoff_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._base_url = f"https://{subdomain}.amocrm.ru/api/v4"
        self._tokens = tokens
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    async def get(self, path: str, params: Params | None = None) -> dict[str, Any] | None:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: Any) -> dict[str, Any] | None:
        return await self._request("POST", path, json=payload)

    async def delete(self, path: str, payload: Any) -> None:
        await self._request("DELETE", path, json=payload)

    async def _request(
        self, method: str, path: str, *, params: Params | None = None, json: Any = None
    ) -> dict[str, Any] | None:
        url = f"{self._base_url}/{path.lstrip('/')}"
        attempt = 0
        token_refreshed = False
        while True:
            token = await self._tokens.access_token()
            status: int | None = None
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=list(params) if params else None,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
                status = response.status_code
                if status == httpx.codes.NO_CONTENT:
                    return None
                if httpx.codes.is_success(status):
                    data = response.json()
                    return data if isinstance(data, dict) else {"items": data}
                if status == httpx.codes.NOT_FOUND:
                    raise AmoCRMNotFound(f"{method} {path} not found")
            except httpx.HTTPError as exc:
                logger.warning("AmoCRM network error on %s: %s", path, type(exc).__name__)

            if status == httpx.codes.UNAUTHORIZED and not token_refreshed:
                self._tokens.invalidate()
                token_refreshed = True
                continue
            if (status is None or status in _RETRYABLE_STATUSES) and attempt < self._max_retries:
                await self._sleep(self._backoff_s * 2**attempt)
                attempt += 1
                continue
            raise CRMUnavailable(f"AmoCRM {method} {path} failed, status={status}")
