import json

import httpx
import pytest
import respx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.webhooks import (
    AmoCRMWebhookRegistrar,
    resolve_tunnel_url,
    webhook_destination,
)
from app.domain.errors import CRMUnavailable
from tests.unit.test_amocrm_client import API, StubTokens, no_sleep

METRICS = "http://tunnel:2000/quicktunnel"


def registrar(http: httpx.AsyncClient) -> AmoCRMWebhookRegistrar:
    client = AmoCRMClient(http, "demo", StubTokens(), backoff_s=0, sleep=no_sleep)
    return AmoCRMWebhookRegistrar(client)


def test_destination_contains_secret_path() -> None:
    assert (
        webhook_destination("https://abc.trycloudflare.com", "s" * 32)
        == "https://abc.trycloudflare.com/integrations/amocrm/webhook/" + "s" * 32
    )


async def test_register_subscribes_to_lead_notes(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/webhooks").mock(return_value=httpx.Response(201, json={}))
    async with httpx.AsyncClient() as http:
        await registrar(http).register("https://x/hook")
    assert json.loads(route.calls[0].request.content) == {
        "destination": "https://x/hook",
        "settings": ["note_lead"],
        "sort": 10,
    }


async def test_unregister(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.delete(f"{API}/webhooks").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        await registrar(http).unregister("https://x/hook")
    assert json.loads(route.calls[0].request.content) == {"destination": "https://x/hook"}


async def test_tunnel_url_waits_until_hostname_appears(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(METRICS).mock(
        side_effect=[
            httpx.ConnectError("not yet"),
            httpx.Response(200, json={"hostname": ""}),
            httpx.Response(200, json={"hostname": "abc.trycloudflare.com"}),
        ]
    )
    async with httpx.AsyncClient() as http:
        url = await resolve_tunnel_url(http, METRICS, delay_s=0, sleep=no_sleep)
    assert url == "https://abc.trycloudflare.com"


async def test_tunnel_never_ready(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(METRICS).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(CRMUnavailable):
            await resolve_tunnel_url(http, METRICS, attempts=3, delay_s=0, sleep=no_sleep)
