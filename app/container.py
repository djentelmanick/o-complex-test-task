from dataclasses import dataclass

from app.application.answer_inquiry import AnswerInquiryUseCase
from app.application.ingest_knowledge import IngestKnowledgeUseCase
from app.application.ports import CRMGateway, KnowledgeRepository


@dataclass(frozen=True, slots=True)
class Container:
    answer_inquiry: AnswerInquiryUseCase
    ingest_knowledge: IngestKnowledgeUseCase
    crm: CRMGateway
    knowledge: KnowledgeRepository
