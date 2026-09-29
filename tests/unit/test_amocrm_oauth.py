import asyncio
import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.adapters.outbound.amocrm.errors import AmoCRMAuthError
from app.adapters.outbound.amocrm.oauth import AmoCRMOAuth
from app.adapters.outbound.amocrm.tokens import TokenPair
from app.domain.errors import CRMUnavailable
from tests.fakes import InMemoryTokenStore

TOKEN_URL = "https://demo.amocrm.ru/oauth2/access_token"
NOW = 1_000_000.0


def tokens(access: str, refresh: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "token_type": "Bearer",
            "expires_in": 86400,
            "access_token": access,
            "refresh_token": refresh,
        },
    )


def make(http: httpx.AsyncClient, store: InMemoryTokenStore, now: list[float]) -> AmoCRMOAuth:
    return AmoCRMOAuth(
        http,
        subdomain="demo",
        client_id="cid",
        client_secret=SecretStr("csecret"),
        redirect_uri="https://example.com",
        store=store,
        clock=lambda: now[0],
    )


async def test_exchange_code_saves_token_pair(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(TOKEN_URL).mock(return_value=tokens("a1", "r1"))
    store = InMemoryTokenStore()
    async with httpx.AsyncClient() as http:
        await make(http, store, [NOW]).exchange_code("auth-code")
    assert store.saved == [TokenPair("a1", "r1", NOW + 86400)]
    assert json.loads(route.calls[0].request.content) == {
        "client_id": "cid",
        "client_secret": "csecret",
        "redirect_uri": "https://example.com",
        "grant_type": "authorization_code",
        "code": "auth-code",
    }


async def test_fresh_token_is_served_without_http(respx_mock: respx.MockRouter) -> None:
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW + 3600))
    async with httpx.AsyncClient() as http:
        assert await make(http, store, [NOW]).access_token() == "a1"
    assert respx_mock.calls.call_count == 0


async def test_expired_token_is_refreshed_and_rotation_saved(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(TOKEN_URL).mock(return_value=tokens("a2", "r2"))
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW + 30))
    async with httpx.AsyncClient() as http:
        assert await make(http, store, [NOW]).access_token() == "a2"
    body = json.loads(route.calls[0].request.content)
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "r1"
    assert store.saved[-1] == TokenPair("a2", "r2", NOW + 86400)


async def test_concurrent_callers_refresh_once(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(TOKEN_URL).mock(return_value=tokens("a2", "r2"))
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW - 1))
    async with httpx.AsyncClient() as http:
        oauth = make(http, store, [NOW])
        results = await asyncio.gather(*(oauth.access_token() for _ in range(10)))
    assert set(results) == {"a2"}
    assert route.call_count == 1


async def test_invalidate_forces_refresh(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=tokens("a2", "r2"))
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW + 3600))
    async with httpx.AsyncClient() as http:
        oauth = make(http, store, [NOW])
        assert await oauth.access_token() == "a1"
        oauth.invalidate()
        assert await oauth.access_token() == "a2"


async def test_not_authorized_yet() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(AmoCRMAuthError, match="amocrm-auth"):
            await make(http, InMemoryTokenStore(), [NOW]).access_token()


async def test_rejected_refresh_token_asks_for_new_code(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"hint": "Token has been revoked"})
    )
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW - 1))
    async with httpx.AsyncClient() as http:
        with pytest.raises(AmoCRMAuthError, match="amocrm-auth"):
            await make(http, store, [NOW]).access_token()


async def test_network_error_is_crm_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW - 1))
    async with httpx.AsyncClient() as http:
        with pytest.raises(CRMUnavailable):
            await make(http, store, [NOW]).access_token()
