import asyncio
import uuid

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.adapters.outbound.gigachat.auth import GigaChatTokenProvider
from app.domain.errors import LLMUnavailable

AUTH_URL = "https://auth.test/api/v2/oauth"
NOW = 1_000_000.0


def token_response(token: str, ttl_s: float = 1800) -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": token, "expires_at": int((NOW + ttl_s) * 1000)}
    )


def make(http: httpx.AsyncClient, now: list[float]) -> GigaChatTokenProvider:
    return GigaChatTokenProvider(
        http, AUTH_URL, SecretStr("auth-key"), "GIGACHAT_API_PERS", clock=lambda: now[0]
    )


async def test_fetches_token_with_required_headers_and_caches_it(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(AUTH_URL).mock(return_value=token_response("t1"))
    async with httpx.AsyncClient() as http:
        provider = make(http, [NOW])
        assert await provider.get() == "t1"
        assert await provider.get() == "t1"
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Basic auth-key"
    uuid.UUID(request.headers["RqUID"])
    assert request.content == b"scope=GIGACHAT_API_PERS"


async def test_refreshes_token_before_expiry(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(AUTH_URL).mock(side_effect=[token_response("t1"), token_response("t2")])
    now = [NOW]
    async with httpx.AsyncClient() as http:
        provider = make(http, now)
        assert await provider.get() == "t1"
        now[0] = NOW + 1800 - 30
        assert await provider.get() == "t2"


async def test_concurrent_callers_share_one_token_request(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(AUTH_URL).mock(return_value=token_response("t1"))
    async with httpx.AsyncClient() as http:
        provider = make(http, [NOW])
        tokens = await asyncio.gather(*(provider.get() for _ in range(10)))
    assert set(tokens) == {"t1"}
    assert route.call_count == 1


async def test_invalidate_forces_new_token(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(AUTH_URL).mock(side_effect=[token_response("t1"), token_response("t2")])
    async with httpx.AsyncClient() as http:
        provider = make(http, [NOW])
        await provider.get()
        provider.invalidate()
        assert await provider.get() == "t2"


async def test_auth_error_raises_llm_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(AUTH_URL).mock(return_value=httpx.Response(401, json={"message": "bad"}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMUnavailable):
            await make(http, [NOW]).get()


async def test_network_error_raises_llm_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(AUTH_URL).mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMUnavailable):
            await make(http, [NOW]).get()
