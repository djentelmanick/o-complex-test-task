import logging
from collections.abc import Sequence

from app.application.output import ANSWER_SCHEMA, FUNCTION_NAME, fallback_answer, parse_answer
from app.application.ports import CRMGateway, Embedder, KnowledgeRepository, LLMClient
from app.application.prompts import SYSTEM_PROMPT, build_user_prompt
from app.domain.errors import LLMInvalidOutput
from app.domain.models import (
    AssistantAnswer,
    DialogMessage,
    Inquiry,
    RetrievedChunk,
    Role,
    TokenUsage,
)

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2
_CONTEXT_LIMIT = 2
_CONTEXT_CLIENT_MESSAGES = 3


class AnswerInquiryUseCase:
    def __init__(
        self,
        crm: CRMGateway,
        embedder: Embedder,
        knowledge: KnowledgeRepository,
        llm: LLMClient,
        *,
        retrieval_limit: int = 4,
        min_score: float = 0.3,
        dialog_max_messages: int = 10,
    ) -> None:
        self._crm = crm
        self._embedder = embedder
        self._knowledge = knowledge
        self._llm = llm
        self._retrieval_limit = retrieval_limit
        self._min_score = min_score
        self._dialog_max_messages = dialog_max_messages

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    async def execute(self, inquiry: Inquiry) -> AssistantAnswer:
        lead = await self._crm.get_lead(inquiry.lead_id)
        return await self.answer(inquiry.message, lead.dialog)

    async def answer(self, message: str, dialog: Sequence[DialogMessage]) -> AssistantAnswer:
        recent = tuple(dialog)[-self._dialog_max_messages :]
        retrieved = await self._retrieve(message, recent)
        user_prompt = build_user_prompt(message, recent, retrieved)

        usage = TokenUsage()
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                result = await self._llm.complete_structured(
                    SYSTEM_PROMPT, user_prompt, FUNCTION_NAME, ANSWER_SCHEMA
                )
                usage += result.usage
                return parse_answer(result.arguments, retrieved, usage)
            except LLMInvalidOutput as exc:
                logger.warning("invalid LLM output (attempt %d): %s", attempt, exc)
        return fallback_answer(usage)

    async def _retrieve(
        self, message: str, dialog: tuple[DialogMessage, ...]
    ) -> list[RetrievedChunk]:
        # Вопрос клиента находит статьи для ответа, а недавние реплики из CRM — статьи
        # для допродажи (возражения, этап курса), которых по самому вопросу не найти
        client_lines = [m.text for m in dialog if m.role is Role.CLIENT]
        context = "\n".join(client_lines[-_CONTEXT_CLIENT_MESSAGES:])
        vectors = await self._embedder.embed([message, context] if context else [message])

        hits = await self._knowledge.search(vectors[0], self._retrieval_limit, self._min_score)
        if context:
            hits += await self._knowledge.search(vectors[1], _CONTEXT_LIMIT, self._min_score)

        best: dict[str, RetrievedChunk] = {}
        for hit in hits:
            if hit.chunk.id not in best or hit.score > best[hit.chunk.id].score:
                best[hit.chunk.id] = hit
        return sorted(best.values(), key=lambda h: h.score, reverse=True)
