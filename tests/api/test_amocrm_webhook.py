import asyncio
import logging
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.container import Container
from app.domain.errors import CRMUnavailable
from tests.api.conftest import API_KEY, make_client, make_container, make_settings

SECRET = "w" * 32
HOOK = f"/integrations/amocrm/webhook/{SECRET}"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def amocrm_settings(**overrides: object) -> Settings:
    return make_settings(
        crm_provider="amocrm",
        amocrm_subdomain="demo",
        amocrm_client_id="cid",
        amocrm_client_secret="csecret",
        amocrm_token_key=Fernet.generate_key().decode(),
        amocrm_webhook_secret=SECRET,
        **overrides,
    )


class RecordingHandler:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    async def execute(self, lead_id: str, message_id: str) -> str:
        self.calls.append((lead_id, message_id))
        if self.fail:
            raise RuntimeError("boom")
        return "processed"


def payload(*notes: tuple[int, int], subdomain: str = "demo") -> str:
    fields: dict[str, str] = {"account[subdomain]": subdomain, "account[id]": "1"}
    for i, (lead_id, note_id) in enumerate(notes):
        fields[f"leads[note][{i}][note][id]"] = str(note_id)
        fields[f"leads[note][{i}][note][element_id]"] = str(lead_id)
        fields[f"leads[note][{i}][note][note_type]"] = "102"
    return urlencode(fields)


async def container_with(handler: RecordingHandler) -> Container:
    container = await make_container()
    return Container(
        answer_inquiry=container.answer_inquiry,
        ingest_knowledge=container.ingest_knowledge,
        crm=container.crm,
        knowledge=container.knowledge,
        handle_incoming=handler,  # type: ignore[arg-type]
    )


async def test_batched_webhook_schedules_every_note() -> None:
    handler = RecordingHandler()
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        response = await client.post(HOOK, content=payload((7, 101), (8, 102)), headers=FORM)
    assert response.status_code == 200
    assert response.json() == {"accepted": 2}
    assert handler.calls == [("7", "101"), ("8", "102")]


async def test_wrong_secret_is_404() -> None:
    handler = RecordingHandler()
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        response = await client.post(
            "/integrations/amocrm/webhook/" + "x" * 32, content=payload((7, 1)), headers=FORM
        )
    assert response.status_code == 404
    assert handler.calls == []


async def test_foreign_account_is_403() -> None:
    handler = RecordingHandler()
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        response = await client.post(HOOK, content=payload((7, 1), subdomain="evil"), headers=FORM)
    assert response.status_code == 403
    assert handler.calls == []


async def test_handler_failure_does_not_break_webhook(caplog: pytest.LogCaptureFixture) -> None:
    handler = RecordingHandler(fail=True)
    with caplog.at_level(logging.ERROR):
        async with make_client(amocrm_settings(), await container_with(handler)) as client:
            response = await client.post(HOOK, content=payload((7, 1)), headers=FORM)
    assert response.status_code == 200
    assert "amocrm note handling failed" in caplog.text


async def test_webhook_route_absent_in_mock_mode() -> None:
    async with make_client() as client:
        response = await client.post(HOOK, content=payload((7, 1)), headers=FORM)
    assert response.status_code == 404


async def test_tunnel_requests_reach_only_the_webhook() -> None:
    handler = RecordingHandler()
    tunnel = {"Cf-Connecting-Ip": "203.0.113.5", "Cf-Ray": "abc"}
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        api = await client.get("/api/v1/leads", headers={**tunnel, "X-API-Key": API_KEY})
        page = await client.get("/", headers=tunnel)
        hook = await client.post(HOOK, content=payload((7, 1)), headers={**FORM, **tunnel})
    assert api.status_code == page.status_code == 404
    assert hook.status_code == 200


async def test_webhook_rate_limit_is_per_cloudflare_client() -> None:
    handler = RecordingHandler()
    settings = amocrm_settings(amocrm_webhook_rate_limit="1/minute")
    async with make_client(settings, await container_with(handler)) as client:

        async def send(ip: str) -> int:
            headers = {**FORM, "Cf-Connecting-Ip": ip}
            response = await client.post(HOOK, content=payload((7, 1)), headers=headers)
            return response.status_code

        codes = [await send("203.0.113.1"), await send("203.0.113.2"), await send("203.0.113.1")]
    assert codes == [200, 200, 429]


async def test_crm_outage_on_api_is_503() -> None:
    container = await make_container()

    async def broken(_: str) -> None:
        raise CRMUnavailable("amocrm down")

    container.crm.get_lead = broken  # type: ignore[method-assign]
    async with make_client(container=container) as client:
        response = await client.post(
            "/api/v1/inquiries",
            json={"lead_id": "lead-1", "message": "Как принимать?"},
            headers={"X-API-Key": API_KEY},
        )
    assert response.status_code == 503
    assert "amocrm down" not in response.text


async def test_any_proxied_request_reaches_only_the_webhook() -> None:
    handler = RecordingHandler()
    proxied = {"X-Forwarded-For": "203.0.113.9"}
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        api = await client.get("/api/v1/leads", headers={**proxied, "X-API-Key": API_KEY})
        hook = await client.post(HOOK, content=payload((7, 1)), headers={**FORM, **proxied})
    assert api.status_code == 404
    assert hook.status_code == 200


async def test_webhook_rate_limit_uses_forwarded_client_ip() -> None:
    handler = RecordingHandler()
    settings = amocrm_settings(amocrm_webhook_rate_limit="1/minute")
    async with make_client(settings, await container_with(handler)) as client:

        async def send(forwarded: str) -> int:
            headers = {**FORM, "X-Forwarded-For": forwarded}
            response = await client.post(HOOK, content=payload((7, 1)), headers=headers)
            return response.status_code

        codes = [
            await send("203.0.113.1"),
            await send("203.0.113.2, 10.0.0.1"),
            await send("203.0.113.1"),
        ]
    assert codes == [200, 200, 429]


class ConcurrencyTrackingHandler:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def execute(self, lead_id: str, message_id: str) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        return "processed"


async def test_webhook_events_are_processed_one_at_a_time() -> None:
    handler = ConcurrencyTrackingHandler()
    container = await container_with(handler)  # type: ignore[arg-type]
    async with make_client(amocrm_settings(), container) as client:
        await asyncio.gather(
            client.post(HOOK, content=payload((7, 1), (7, 2)), headers=FORM),
            client.post(HOOK, content=payload((8, 3)), headers=FORM),
        )
    assert handler.max_active == 1
