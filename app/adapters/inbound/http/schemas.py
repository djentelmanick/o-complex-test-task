from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import AssistantAnswer, Lead

LEAD_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
MESSAGE_MAX_LENGTH = 2000


class InquiryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    lead_id: str = Field(pattern=LEAD_ID_PATTERN)
    message: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)


class SourceOut(BaseModel):
    doc_id: str
    title: str


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class InquiryResponse(BaseModel):
    client_reply: str
    manager_hint: str
    sources: list[SourceOut]
    usage: UsageOut
    latency_ms: int
    fallback: bool

    @classmethod
    def from_domain(cls, answer: AssistantAnswer, latency_ms: int) -> "InquiryResponse":
        return cls(
            client_reply=answer.client_reply,
            manager_hint=answer.manager_hint,
            sources=[SourceOut(doc_id=s.doc_id, title=s.title) for s in answer.sources],
            usage=UsageOut(
                prompt_tokens=answer.usage.prompt,
                completion_tokens=answer.usage.completion,
                total_tokens=answer.usage.total,
            ),
            latency_ms=latency_ms,
            fallback=answer.fallback,
        )


class DialogMessageOut(BaseModel):
    role: str
    text: str


class LeadOut(BaseModel):
    id: str
    name: str
    dialog: list[DialogMessageOut]

    @classmethod
    def from_domain(cls, lead: Lead) -> "LeadOut":
        return cls(
            id=lead.id,
            name=lead.name,
            dialog=[DialogMessageOut(role=m.role.value, text=m.text) for m in lead.dialog],
        )
