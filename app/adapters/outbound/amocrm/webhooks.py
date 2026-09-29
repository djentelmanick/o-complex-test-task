import asyncio
from collections.abc import Awaitable, Callable

import httpx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.domain.errors import CRMUnavailable

WEBHOOK_EVENTS = ["note_lead"]
WEBHOOK_PATH_PREFIX = "/integrations/amocrm/webhook/"
_PROBE_TIMEOUT_S = 2.0


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

    async def destinations_with_secret(self, secret: str) -> list[str]:
        data = await self._client.get("/webhooks")
        hooks = (data or {}).get("_embedded", {}).get("webhooks", [])
        suffix = f"{WEBHOOK_PATH_PREFIX}{secret}"
        return [
            str(hook["destination"])
            for hook in hooks
            if isinstance(hook, dict) and str(hook.get("destination", "")).endswith(suffix)
        ]


async def resolve_tunnel_url(
    http: httpx.AsyncClient,
    metrics_url: str,
    *,
    attempts: int = 15,
    delay_s: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    # Адрес туннеля может меняться между запусками — спрашиваем его у агента туннеля
    for _ in range(attempts):
        try:
            # Туннель поднимается после приложения: не ждём общий таймаут клиента на каждой попытке
            response = await http.get(metrics_url, timeout=_PROBE_TIMEOUT_S)
            if response.status_code == httpx.codes.OK:
                public_url = _public_url(response.json())
                if public_url:
                    return public_url
        except (httpx.HTTPError, ValueError):
            pass
        await sleep(delay_s)
    raise CRMUnavailable("tunnel agent did not report a public URL")


def _public_url(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    # cloudflared quick tunnel: {"hostname": "..."}
    if data.get("hostname"):
        return f"https://{data['hostname']}"
    # ngrok agent API: {"tunnels": [{"public_url": "https://..."}]}
    for tunnel in data.get("tunnels") or []:
        url = tunnel.get("public_url") if isinstance(tunnel, dict) else None
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return None
