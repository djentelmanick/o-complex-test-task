import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from app.application.ports import LLMResult
from app.domain.errors import LLMInvalidOutput, LLMUnavailable
from app.domain.models import TokenUsage

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class TokenSource(Protocol):
    async def get(self) -> str: ...

    def invalidate(self) -> None: ...


class GigaChatClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        tokens: TokenSource,
        *,
        max_retries: int = 2,
        backoff_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._tokens = tokens
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        attempt = 0
        token_refreshed = False
        while True:
            token = await self._tokens.get()
            status: int | None = None
            try:
                response = await self._http.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
                status = response.status_code
                if status == httpx.codes.OK:
                    return _json_object(response)
            except httpx.HTTPError as exc:
                logger.warning("GigaChat network error on %s: %s", path, type(exc).__name__)

            if status == httpx.codes.UNAUTHORIZED and not token_refreshed:
                self._tokens.invalidate()
                token_refreshed = True
                continue
            if (status is None or status in _RETRYABLE_STATUSES) and attempt < self._max_retries:
                await self._sleep(self._backoff_s * 2**attempt)
                attempt += 1
                continue
            logger.warning("GigaChat request to %s failed, status=%s", path, status)
            raise LLMUnavailable(f"GigaChat request to {path} failed, status={status}")


class GigaChatLLM:
    def __init__(self, client: GigaChatClient, model: str, *, temperature: float = 0.3) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature

    async def complete_structured(
        self, system: str, user: str, function_name: str, schema: dict[str, Any]
    ) -> LLMResult:
        data = await self._client.post_json(
            "/chat/completions",
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "functions": [
                    {
                        "name": function_name,
                        "description": "Передать ответ клиенту и подсказку менеджеру",
                        "parameters": schema,
                    }
                ],
                "function_call": {"name": function_name},
                "temperature": self._temperature,
            },
        )
        return LLMResult(arguments=_function_arguments(data, function_name), usage=_usage(data))


class GigaChatEmbedder:
    def __init__(self, client: GigaChatClient, model: str) -> None:
        self._client = client
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._client.post_json("/embeddings", {"model": self._model, "input": texts})
        try:
            items = sorted(data["data"], key=lambda item: item["index"])
            return [[float(x) for x in item["embedding"]] for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMUnavailable("unexpected GigaChat embeddings response") from exc


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMUnavailable("GigaChat returned non-JSON response") from exc
    if not isinstance(data, dict):
        raise LLMUnavailable("GigaChat returned unexpected JSON")
    return data


def _function_arguments(data: dict[str, Any], function_name: str) -> dict[str, Any]:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMInvalidOutput("response has no choices") from exc
    call = message.get("function_call") if isinstance(message, dict) else None
    if not isinstance(call, dict) or call.get("name") != function_name:
        raise LLMInvalidOutput("model did not call the expected function")
    arguments = call.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise LLMInvalidOutput("function arguments are not valid JSON") from exc
    if not isinstance(arguments, dict):
        raise LLMInvalidOutput("function arguments are not an object")
    return arguments


def _usage(data: dict[str, Any]) -> TokenUsage:
    usage = data.get("usage") or {}
    return TokenUsage(
        prompt=int(usage.get("prompt_tokens", 0)),
        completion=int(usage.get("completion_tokens", 0)),
    )
