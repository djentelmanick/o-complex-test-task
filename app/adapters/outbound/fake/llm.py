import re
from typing import Any

from app.application.ports import LLMResult
from app.domain.models import TokenUsage

_CHUNK_ID = re.compile(r'<chunk id="([^"]+)">')


class FakeLLM:
    """Заглушка LLM: позволяет прогнать весь пайплайн без ключа GigaChat."""

    async def complete_structured(
        self, system: str, user: str, function_name: str, schema: dict[str, Any]
    ) -> LLMResult:
        chunk_ids = _CHUNK_ID.findall(user)
        if chunk_ids:
            client_reply = (
                "Здравствуйте! Спасибо за вопрос. Сейчас сервис работает в демо-режиме "
                "без языковой модели — ниже указаны статьи базы знаний по вашему обращению."
            )
        else:
            client_reply = (
                "Здравствуйте! Спасибо за вопрос. Уточню информацию у специалиста "
                "и вернусь с ответом."
            )
        manager_hint = (
            "Демо-режим (LLM_PROVIDER=fake): подключите GigaChat, "
            "чтобы получить подсказку по допродаже."
        )
        usage = TokenUsage(
            prompt=len(system + user) // 4,
            completion=len(client_reply + manager_hint) // 4,
        )
        arguments = {
            "client_reply": client_reply,
            "manager_hint": manager_hint,
            "used_chunk_ids": chunk_ids[:2],
        }
        return LLMResult(arguments=arguments, usage=usage)
