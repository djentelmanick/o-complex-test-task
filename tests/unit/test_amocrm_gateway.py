import json

import httpx
import pytest
import respx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.gateway import (
    DEMO_TAG,
    SERVICE_NAME,
    UNAVAILABLE_TEXT,
    AmoCRMGateway,
    format_answer_note,
)
from app.domain.errors import LeadNotFound
from app.domain.models import AssistantAnswer, Role, Source, TokenUsage
from tests.unit.test_amocrm_client import API, StubTokens, no_sleep

ANSWER = AssistantAnswer(
    client_reply="Здравствуйте! Принимайте курсом.",
    manager_hint="Предложите Zeolite Mini.",
    sources=(Source("zeolite-mini", "Zeolite Mini"), Source("objections", "Возражения")),
    usage=TokenUsage(prompt=1, completion=1),
)


def note(note_id: int, note_type: str, text: str) -> dict[str, object]:
    return {"id": note_id, "entity_id": 7, "note_type": note_type, "params": {"text": text}}


def notes_page(notes: list[dict[str, object]], has_next: bool = False) -> httpx.Response:
    links: dict[str, object] = {"self": {"href": "x"}}
    if has_next:
        links["next"] = {"href": "y"}
    return httpx.Response(200, json={"_links": links, "_embedded": {"notes": notes}})


def gateway(http: httpx.AsyncClient) -> AmoCRMGateway:
    return AmoCRMGateway(AmoCRMClient(http, "demo", StubTokens(), backoff_s=0, sleep=no_sleep))


async def test_lead_dialog_maps_sms_notes_and_skips_others(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "name": "Марина"})
    )
    notes_route = respx_mock.get(f"{API}/leads/7/notes").mock(
        side_effect=[
            notes_page(
                [note(1, "sms_in", "Дорого"), note(2, "service_message", "наш ответ")],
                has_next=True,
            ),
            notes_page([note(3, "sms_out", "Есть Mini"), note(4, "common", "внутреннее")]),
        ]
    )
    async with httpx.AsyncClient() as http:
        lead = await gateway(http).get_lead("7")

    assert lead.id == "7"
    assert lead.name == "Марина"
    assert [(m.id, m.role, m.text) for m in lead.dialog] == [
        ("1", Role.CLIENT, "Дорого"),
        ("3", Role.MANAGER, "Есть Mini"),
    ]
    first = notes_route.calls[0].request.url.params
    assert first.get_list("filter[note_type][]") == ["sms_in", "sms_out"]
    assert first["order[id]"] == "asc"
    assert notes_route.calls[1].request.url.params["page"] == "2"


async def test_lead_without_notes(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads/7").mock(return_value=httpx.Response(200, json={"id": 7}))
    respx_mock.get(f"{API}/leads/7/notes").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        lead = await gateway(http).get_lead("7")
    assert lead.dialog == ()
    assert lead.name == "Сделка 7"


@pytest.mark.parametrize("lead_id", ["abc", "../1", ""])
async def test_non_numeric_lead_id_is_not_found(lead_id: str) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(LeadNotFound):
            await gateway(http).get_lead(lead_id)


async def test_missing_lead_is_not_found(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads/7").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LeadNotFound):
            await gateway(http).get_lead("7")


async def test_list_leads_takes_recent_ones(respx_mock: respx.MockRouter) -> None:
    list_route = respx_mock.get(f"{API}/leads").mock(
        return_value=httpx.Response(200, json={"_embedded": {"leads": [{"id": 7}]}})
    )
    respx_mock.get(f"{API}/leads/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "name": "A"})
    )
    respx_mock.get(f"{API}/leads/7/notes").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        leads = await gateway(http).list_leads()
    assert [lead.id for lead in leads] == ["7"]
    params = list_route.calls[0].request.url.params
    assert params["order[updated_at]"] == "desc"
    assert params["limit"] == "5"


async def test_publish_writes_service_message(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/leads/notes").mock(
        return_value=httpx.Response(200, json={"_embedded": {"notes": [{"id": 9}]}})
    )
    async with httpx.AsyncClient() as http:
        await gateway(http).publish("7", ANSWER)
    [body] = json.loads(route.calls[0].request.content)
    assert body["entity_id"] == 7
    assert body["note_type"] == "extended_service_message"
    assert body["params"] == {"service": SERVICE_NAME, "text": format_answer_note(ANSWER)}


async def test_fallback_answer_is_published_as_unavailable(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/leads/notes").mock(
        return_value=httpx.Response(200, json={"_embedded": {"notes": [{"id": 9}]}})
    )
    fallback = AssistantAnswer("x", "y", (), TokenUsage(), fallback=True)
    async with httpx.AsyncClient() as http:
        await gateway(http).publish("7", fallback)
        await gateway(http).publish_unavailable("7")
    texts = [json.loads(c.request.content)[0]["params"]["text"] for c in route.calls]
    assert texts == [UNAVAILABLE_TEXT, UNAVAILABLE_TEXT]


def test_note_text_has_reply_sources_and_hint() -> None:
    text = format_answer_note(ANSWER)
    assert text.index("Здравствуйте!") < text.index("Zeolite Mini, Возражения")
    assert text.index("Zeolite Mini, Возражения") < text.index("Предложите Zeolite Mini.")


async def test_demo_helpers_create_lead_and_messages(respx_mock: respx.MockRouter) -> None:
    leads_route = respx_mock.post(f"{API}/leads").mock(
        return_value=httpx.Response(200, json={"_embedded": {"leads": [{"id": 55}]}})
    )
    notes_route = respx_mock.post(f"{API}/leads/notes").mock(
        return_value=httpx.Response(200, json={"_embedded": {"notes": [{"id": 77}]}})
    )
    async with httpx.AsyncClient() as http:
        gw = gateway(http)
        assert await gw.create_lead("Анна") == "55"
        assert await gw.add_message("55", Role.CLIENT, "Привет") == "77"
        await gw.add_message("55", Role.MANAGER, "Здравствуйте")
    [lead] = json.loads(leads_route.calls[0].request.content)
    assert lead["_embedded"]["tags"] == [{"name": DEMO_TAG}]
    types = [json.loads(c.request.content)[0]["note_type"] for c in notes_route.calls]
    assert types == ["sms_in", "sms_out"]
