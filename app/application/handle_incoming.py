import logging
from enum import StrEnum

from app.application.answer_inquiry import MAX_MESSAGE_LENGTH, AnswerInquiryUseCase
from app.application.ports import AnswerPublisher, CRMGateway, ProcessedEvents
from app.domain.errors import KnowledgeBaseUnavailable, LeadNotFound, LLMUnavailable
from app.domain.models import Role

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_MESSAGE_LENGTH",
    "HandleIncomingMessageUseCase",
    "HandleResult",
    "message_event_key",
]


def message_event_key(message_id: str) -> str:
    return f"crm:message:{message_id}"


class HandleResult(StrEnum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    UNAVAILABLE = "unavailable"


class HandleIncomingMessageUseCase:
    """Сообщение клиента из CRM → ответ и подсказка обратно в ту же сделку."""

    def __init__(
        self,
        crm: CRMGateway,
        answer_inquiry: AnswerInquiryUseCase,
        publisher: AnswerPublisher,
        processed_events: ProcessedEvents,
    ) -> None:
        self._crm = crm
        self._answer_inquiry = answer_inquiry
        self._publisher = publisher
        self._processed_events = processed_events

    async def execute(self, lead_id: str, message_id: str) -> HandleResult:
        # CRM может доставить событие повторно — отвечать клиенту дважды нельзя
        if not await self._processed_events.first_seen(message_event_key(message_id)):
            return HandleResult.DUPLICATE
        try:
            lead = await self._crm.get_lead(lead_id)
        except LeadNotFound:
            logger.warning("lead %s disappeared before processing", lead_id)
            return HandleResult.IGNORED

        position = next((i for i, m in enumerate(lead.dialog) if m.id == message_id), None)
        if position is None or lead.dialog[position].role is not Role.CLIENT:
            return HandleResult.IGNORED
        text = lead.dialog[position].text.strip()[:MAX_MESSAGE_LENGTH]
        if not text:
            return HandleResult.IGNORED

        try:
            answer = await self._answer_inquiry.answer(text, lead.dialog[:position])
        except (LLMUnavailable, KnowledgeBaseUnavailable) as exc:
            logger.error("assistant unavailable for lead %s: %s", lead_id, exc)
            await self._publisher.publish_unavailable(lead_id)
            return HandleResult.UNAVAILABLE
        await self._publisher.publish(lead_id, answer)
        return HandleResult.PROCESSED
