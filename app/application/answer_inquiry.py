import logging

from app.application.output import ANSWER_SCHEMA, FUNCTION_NAME, fallback_answer, parse_answer
from app.application.ports import CRMGateway, Embedder, KnowledgeRepository, LLMClient
from app.application.prompts import SYSTEM_PROMPT, build_user_prompt
from app.domain.errors import LLMInvalidOutput
from app.domain.models import AssistantAnswer, Inquiry, TokenUsage

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2


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

    async def execute(self, inquiry: Inquiry) -> AssistantAnswer:
        lead = await self._crm.get_lead(inquiry.lead_id)
        dialog = lead.dialog[-self._dialog_max_messages :]
        [vector] = await self._embedder.embed([inquiry.message])
        retrieved = await self._knowledge.search(vector, self._retrieval_limit, self._min_score)
        user_prompt = build_user_prompt(inquiry.message, dialog, retrieved)

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
