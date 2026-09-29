import logging
import re
import secrets
from collections.abc import Iterable
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from slowapi import Limiter

from app.adapters.inbound.http.routes import get_container
from app.adapters.outbound.amocrm.webhooks import WEBHOOK_PATH_PREFIX
from app.application.handle_incoming import HandleIncomingMessageUseCase
from app.config import Settings

logger = logging.getLogger(__name__)

_NOTE_FIELD = re.compile(r"^leads\[note\]\[(\d+)\]\[note\]\[(id|element_id)\]$")


def parse_note_events(form: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Пары (lead_id, note_id) из form-payload вебхука note_lead; AmoCRM шлёт их пачкой."""
    grouped: dict[int, dict[str, str]] = {}
    for key, value in form:
        match = _NOTE_FIELD.match(key)
        if match and value.isdigit():
            grouped.setdefault(int(match.group(1)), {})[match.group(2)] = value
    return [
        (fields["element_id"], fields["id"])
        for _, fields in sorted(grouped.items())
        if "id" in fields and "element_id" in fields
    ]


def build_amocrm_router(settings: Settings, limiter: Limiter) -> APIRouter:
    if settings.amocrm_webhook_secret is None:
        raise RuntimeError("AMOCRM_WEBHOOK_SECRET is not set")
    expected_secret = settings.amocrm_webhook_secret.get_secret_value().encode()
    router = APIRouter()

    @router.post(WEBHOOK_PATH_PREFIX + "{token}", include_in_schema=False)
    @limiter.limit(settings.amocrm_webhook_rate_limit)
    async def amocrm_webhook(
        request: Request, token: str, background: BackgroundTasks
    ) -> dict[str, int]:
        if not secrets.compare_digest(token.encode(), expected_secret):
            raise HTTPException(status_code=404, detail="Not Found")
        body = (await request.body()).decode("utf-8", "replace")
        form = parse_qsl(body, keep_blank_values=True)
        if dict(form).get("account[subdomain]") != settings.amocrm_subdomain:
            raise HTTPException(status_code=403, detail="Вебхук от другого аккаунта AmoCRM")
        use_case = get_container(request).handle_incoming
        if use_case is None:
            raise HTTPException(status_code=404, detail="Not Found")
        events = parse_note_events(form)
        # AmoCRM ждёт ответ за пару секунд, а генерация идёт дольше — обрабатываем после ответа
        for lead_id, note_id in events:
            background.add_task(_handle_safely, use_case, lead_id, note_id)
        return {"accepted": len(events)}

    return router


async def _handle_safely(
    use_case: HandleIncomingMessageUseCase, lead_id: str, note_id: str
) -> None:
    try:
        result = await use_case.execute(lead_id, note_id)
        logger.info(
            "amocrm note processed lead_id=%s note_id=%s result=%s", lead_id, note_id, result
        )
    except Exception:
        logger.exception("amocrm note handling failed lead_id=%s note_id=%s", lead_id, note_id)
