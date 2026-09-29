import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.errors import LLMInvalidOutput
from app.domain.models import (
    AssistantAnswer,
    KnowledgeChunk,
    RetrievedChunk,
    Source,
    TokenUsage,
)

FUNCTION_NAME = "submit_answer"
CLIENT_REPLY_MAX = 1500
MANAGER_HINT_MAX = 800
FALLBACK_SOURCES_LIMIT = 2

FALLBACK_CLIENT_REPLY = (
    "Спасибо за обращение! Передаю ваш вопрос менеджеру, он скоро с вами свяжется."
)
FALLBACK_MANAGER_HINT = "Автоответ не сформирован — ответьте клиенту вручную."

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "client_reply": {"type": "string", "description": "Вежливый ответ клиенту"},
        "manager_hint": {
            "type": "string",
            "description": "Подсказка менеджеру по допродаже с обоснованием",
        },
        "used_chunk_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "id фрагментов базы знаний, на которые опирается ответ",
        },
    },
    "required": ["client_reply", "manager_hint", "used_chunk_ids"],
}


# GigaChat иногда оставляет в конце строки хвост JSON-разметки (`.,`, `.\n",`)
# или копирует экранированные теги из промпта (`‹/client_reply›`)
_TRAILING_ARTIFACTS = re.compile(r"(?:\s*‹/?[a-z_]+›|[\s,])+$")


def _strip_artifacts(text: str) -> str:
    # Переводы строк иногда приходят экранированными дважды — литералом `\n`
    text = _TRAILING_ARTIFACTS.sub("", text.replace("\\n", "\n"))
    # Непарная кавычка в конце — остаток JSON-строки, парную («"Zeolite Max"») не трогаем
    if text.endswith('"') and text.count('"') % 2 == 1:
        text = _TRAILING_ARTIFACTS.sub("", text[:-1])
    return text


class _LLMAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    client_reply: str = Field(min_length=1, max_length=CLIENT_REPLY_MAX)
    manager_hint: str = Field(min_length=1, max_length=MANAGER_HINT_MAX)
    used_chunk_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("client_reply", "manager_hint", mode="before")
    @classmethod
    def _strip_json_artifacts(cls, value: object) -> object:
        return _strip_artifacts(value) if isinstance(value, str) else value


def parse_answer(
    arguments: dict[str, Any],
    retrieved: Sequence[RetrievedChunk],
    usage: TokenUsage,
) -> AssistantAnswer:
    try:
        parsed = _LLMAnswer.model_validate(arguments)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
        raise LLMInvalidOutput(f"invalid fields: {fields}") from exc
    return AssistantAnswer(
        client_reply=parsed.client_reply,
        manager_hint=parsed.manager_hint,
        sources=select_sources(parsed.used_chunk_ids, retrieved),
        usage=usage,
    )


def select_sources(
    used_ids: Sequence[str], retrieved: Sequence[RetrievedChunk]
) -> tuple[Source, ...]:
    # Источники берём только из реально найденных чанков: модель не может сослаться на то,
    # чего ей не показывали
    by_id = {r.chunk.id: r.chunk for r in retrieved}
    chosen: list[KnowledgeChunk] = [by_id[i] for i in used_ids if i in by_id]
    limit: int | None = None
    if not chosen:
        chosen = [r.chunk for r in sorted(retrieved, key=lambda r: r.score, reverse=True)]
        limit = FALLBACK_SOURCES_LIMIT
    unique: dict[str, Source] = {}
    for chunk in chosen:
        unique.setdefault(chunk.doc_id, Source(doc_id=chunk.doc_id, title=chunk.title))
    sources = tuple(unique.values())
    return sources if limit is None else sources[:limit]


def fallback_answer(usage: TokenUsage) -> AssistantAnswer:
    return AssistantAnswer(
        client_reply=FALLBACK_CLIENT_REPLY,
        manager_hint=FALLBACK_MANAGER_HINT,
        sources=(),
        usage=usage,
        fallback=True,
    )
