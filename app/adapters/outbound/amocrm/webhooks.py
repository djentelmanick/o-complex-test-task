import asyncio
from collections.abc import Awaitable, Callable

import httpx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.domain.errors import CRMUnavailable

WEBHOOK_EVENTS = ["note_lead"]
WEBHOOK_PATH_PREFIX = "/integrations/amocrm/webhook/"


def webhook_destination(public_url: str, secret: str) -> str:
    return f"{public_url.rstrip('/')}{WEBHOOK_PATH_PREFIX}{secret}"


class AmoCRMWebhookRegistrar:
    def __init__(self, client: AmoCRMClient) -> None:
        self._client = client

    async def register(self, destination: str) -> None:
        await self._client.post(
            "/webhooks", {"destination": destination, "settings": WEBHOOK_EVENTS, "sort": 10}
        )

    async def unregister(self, destination: str) -> None:
        await self._client.delete("/webhooks", {"destination": destination})


async def resolve_tunnel_url(
    http: httpx.AsyncClient,
    metrics_url: str,
    *,
    attempts: int = 15,
    delay_s: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    # Quick tunnel получает случайный адрес при каждом запуске — узнаём его у cloudflared
    for _ in range(attempts):
        try:
            response = await http.get(metrics_url)
            if response.status_code == httpx.codes.OK:
                hostname = response.json().get("hostname")
                if hostname:
                    return f"https://{hostname}"
        except (httpx.HTTPError, ValueError):
            pass
        await sleep(delay_s)
    raise CRMUnavailable("cloudflared tunnel did not report a public hostname")
