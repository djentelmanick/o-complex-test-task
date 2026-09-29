import json

import httpx
import pytest
import respx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.errors import AmoCRMNotFound
from app.domain.errors import CRMUnavailable

API = "https://demo.amocrm.ru/api/v4"


class StubTokens:
    def __init__(self) -> None:
        self.token = "t1"
        self.invalidated = 0

    async def access_token(self) -> str:
        return self.token

    def invalidate(self) -> None:
        self.invalidated += 1
        self.token = "t2"


async def no_sleep(_: float) -> None:
    return None


def make(http: httpx.AsyncClient, tokens: StubTokens | None = None) -> AmoCRMClient:
    return AmoCRMClient(http, "demo", tokens or StubTokens(), backoff_s=0, sleep=no_sleep)


async def test_get_sends_bearer_and_params(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{API}/leads/1").mock(return_value=httpx.Response(200, json={"id": 1}))
    async with httpx.AsyncClient() as http:
        assert await make(http).get("/leads/1", params=[("with", "contacts")]) == {"id": 1}
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer t1"
    assert request.url.params["with"] == "contacts"


async def test_no_content_is_none(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        assert await make(http).get("/leads") is None


async def test_unauthorized_refreshes_token_once(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/leads/notes").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"ok": True})]
    )
    tokens = StubTokens()
    async with httpx.AsyncClient() as http:
        await make(http, tokens).post("/leads/notes", [{"a": 1}])
    assert tokens.invalidated == 1
    assert route.calls[1].request.headers["Authorization"] == "Bearer t2"
    assert json.loads(route.calls[1].request.content) == [{"a": 1}]


async def test_rate_limit_and_server_errors_are_retried(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{API}/leads").mock(
        side_effect=[httpx.Response(429), httpx.Response(502), httpx.Response(200, json={})]
    )
    async with httpx.AsyncClient() as http:
        await make(http).get("/leads")
    assert route.call_count == 3


async def test_gives_up_with_crm_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(CRMUnavailable):
            await make(http).get("/leads")


async def test_not_found_is_distinct_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads/9").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        with pytest.raises(AmoCRMNotFound):
            await make(http).get("/leads/9")


async def test_delete_sends_json_body(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.delete(f"{API}/webhooks").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        await make(http).delete("/webhooks", {"destination": "https://x"})
    assert json.loads(route.calls[0].request.content) == {"destination": "https://x"}
