from typing import Any

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.errors import AmoCRMNotFound
from app.domain.errors import LeadNotFound
from app.domain.models import AssistantAnswer, DialogMessage, Lead, Role

SERVICE_NAME = "Ассистент O-complex"
UNAVAILABLE_TEXT = "Ассистент временно недоступен — ответьте клиенту вручную."
DEMO_TAG = "o-complex-demo"
LIST_LIMIT = 5
# AmoCRM требует телефон у SMS-примечаний; для демо-сообщений подставляем заглушку
DEMO_PHONE = "+70000000000"

_ROLES = {"sms_in": Role.CLIENT, "sms_out": Role.MANAGER}
_NOTE_TYPES = {Role.CLIENT: "sms_in", Role.MANAGER: "sms_out"}
_PAGE_SIZE = "250"
# Обычный service_message склеивает текст в одну строку, extended сохраняет переносы и подпись
_ANSWER_NOTE_TYPE = "extended_service_message"


def format_answer_note(answer: AssistantAnswer) -> str:
    lines = ["💬 Ответ клиенту:", answer.client_reply]
    if answer.sources:
        lines += ["", "📚 Источники: " + ", ".join(s.title for s in answer.sources)]
    lines += ["", "💡 Подсказка менеджеру:", answer.manager_hint]
    return "\n".join(lines)


class AmoCRMGateway:
    """Сделки AmoCRM как лиды: SMS-примечания — диалог, service_message — ответ ассистента."""

    def __init__(self, client: AmoCRMClient) -> None:
        self._client = client

    async def get_lead(self, lead_id: str) -> Lead:
        number = _lead_number(lead_id)
        try:
            data = await self._client.get(f"/leads/{number}")
        except AmoCRMNotFound:
            raise LeadNotFound(lead_id) from None
        if data is None:
            raise LeadNotFound(lead_id)
        name = data.get("name") or f"Сделка {number}"
        return Lead(id=str(number), name=str(name), dialog=await self._dialog(number))

    async def list_leads(self) -> list[Lead]:
        data = await self._client.get(
            "/leads", params=[("order[updated_at]", "desc"), ("limit", str(LIST_LIMIT))]
        )
        return [await self.get_lead(str(item["id"])) for item in _embedded(data, "leads")]

    async def publish(self, lead_id: str, answer: AssistantAnswer) -> None:
        text = UNAVAILABLE_TEXT if answer.fallback else format_answer_note(answer)
        await self._add_note(lead_id, _ANSWER_NOTE_TYPE, {"service": SERVICE_NAME, "text": text})

    async def publish_unavailable(self, lead_id: str) -> None:
        await self._add_note(
            lead_id, _ANSWER_NOTE_TYPE, {"service": SERVICE_NAME, "text": UNAVAILABLE_TEXT}
        )

    async def create_lead(self, name: str) -> str:
        data = await self._client.post(
            "/leads", [{"name": name, "_embedded": {"tags": [{"name": DEMO_TAG}]}}]
        )
        return str(_embedded(data, "leads")[0]["id"])

    async def add_message(self, lead_id: str, role: Role, text: str) -> str:
        data = await self._add_note(lead_id, _NOTE_TYPES[role], {"text": text, "phone": DEMO_PHONE})
        return str(_embedded(data, "notes")[0]["id"])

    async def _dialog(self, number: int) -> tuple[DialogMessage, ...]:
        messages: list[DialogMessage] = []
        page = 1
        while True:
            data = await self._client.get(
                f"/leads/{number}/notes",
                params=[
                    ("filter[note_type][]", "sms_in"),
                    ("filter[note_type][]", "sms_out"),
                    ("order[id]", "asc"),
                    ("limit", _PAGE_SIZE),
                    ("page", str(page)),
                ],
            )
            for note in _embedded(data, "notes"):
                role = _ROLES.get(str(note.get("note_type")))
                text = (note.get("params") or {}).get("text")
                # Фильтр на стороне API не гарантирован — проверяем тип ещё раз
                if role is not None and isinstance(text, str):
                    messages.append(DialogMessage(role, text, id=str(note["id"])))
            if not (data or {}).get("_links", {}).get("next"):
                return tuple(messages)
            page += 1

    async def _add_note(
        self, lead_id: str, note_type: str, params: dict[str, str]
    ) -> dict[str, Any] | None:
        return await self._client.post(
            "/leads/notes",
            [{"entity_id": _lead_number(lead_id), "note_type": note_type, "params": params}],
        )


def _lead_number(lead_id: str) -> int:
    if not lead_id.isdigit():
        raise LeadNotFound(lead_id)
    return int(lead_id)


def _embedded(data: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    items = (data or {}).get("_embedded", {}).get(key, [])
    return [item for item in items if isinstance(item, dict)]
