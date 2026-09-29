import pytest

from app.adapters.outbound.crm.mock import MockCRMGateway
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.application.answer_inquiry import AnswerInquiryUseCase
from app.application.ports import LLMResult
from app.application.prompts import EMPTY_KNOWLEDGE_MARKER
from app.domain.errors import LeadNotFound, LLMInvalidOutput, LLMUnavailable
from app.domain.models import DialogMessage, Inquiry, Lead, Role, Source, TokenUsage
from tests.fakes import InMemoryKnowledgeRepository, ScriptedLLM, make_chunk, seed

LEAD = Lead(
    id="lead-1",
    name="Тест",
    dialog=tuple(DialogMessage(Role.CLIENT, f"сообщение {i}") for i in range(15)),
)
INQUIRY = Inquiry(lead_id="lead-1", message="Как принимать цеолит для очищения?")


def ok(chunk_ids: list[str], prompt: int = 100) -> LLMResult:
    return LLMResult(
        arguments={
            "client_reply": "Здравствуйте!",
            "manager_hint": "Предложите Mineral Complex",
            "used_chunk_ids": chunk_ids,
        },
        usage=TokenUsage(prompt=prompt, completion=10),
    )


BAD = LLMResult(arguments={"client_reply": ""}, usage=TokenUsage(prompt=50, completion=5))


async def make_use_case(llm: ScriptedLLM, min_score: float = 0.1) -> AnswerInquiryUseCase:
    embedder = FakeEmbedder(dim=256)
    repo = InMemoryKnowledgeRepository()
    await seed(
        repo,
        embedder,
        [
            make_chunk("zeolite", content="Цеолит сорбент: как принимать для очищения курсом"),
            make_chunk("delivery", content="Доставка курьером, оплата картой"),
        ],
    )
    return AnswerInquiryUseCase(
        crm=MockCRMGateway([LEAD]),
        embedder=embedder,
        knowledge=repo,
        llm=llm,
        retrieval_limit=4,
        min_score=min_score,
        dialog_max_messages=10,
    )


async def test_returns_grounded_answer_and_passes_context_to_llm() -> None:
    llm = ScriptedLLM([ok(["zeolite#0"])])
    answer = await (await make_use_case(llm)).execute(INQUIRY)

    assert answer.client_reply == "Здравствуйте!"
    assert answer.sources == (Source("zeolite", "Zeolite"),)
    assert answer.fallback is False
    _, user_prompt = llm.calls[0]
    assert '<chunk id="zeolite#0">' in user_prompt
    assert "Как принимать цеолит" in user_prompt


async def test_only_last_dialog_messages_are_sent() -> None:
    llm = ScriptedLLM([ok([])])
    await (await make_use_case(llm)).execute(INQUIRY)
    _, user_prompt = llm.calls[0]
    assert "сообщение 14" in user_prompt
    assert "сообщение 5" in user_prompt
    assert "сообщение 4" not in user_prompt


async def test_nothing_relevant_marks_empty_knowledge() -> None:
    llm = ScriptedLLM([ok([])])
    await (await make_use_case(llm, min_score=0.99)).execute(INQUIRY)
    _, user_prompt = llm.calls[0]
    assert EMPTY_KNOWLEDGE_MARKER in user_prompt


async def test_invalid_output_is_retried_once_and_usage_summed() -> None:
    llm = ScriptedLLM([BAD, ok(["zeolite#0"], prompt=100)])
    answer = await (await make_use_case(llm)).execute(INQUIRY)
    assert len(llm.calls) == 2
    assert answer.fallback is False
    assert answer.usage == TokenUsage(prompt=150, completion=15)


async def test_adapter_level_invalid_output_is_retried() -> None:
    llm = ScriptedLLM([LLMInvalidOutput("no function call"), ok([])])
    answer = await (await make_use_case(llm)).execute(INQUIRY)
    assert answer.fallback is False


async def test_two_invalid_outputs_give_fallback() -> None:
    llm = ScriptedLLM([BAD, BAD])
    answer = await (await make_use_case(llm)).execute(INQUIRY)
    assert answer.fallback is True
    assert answer.usage == TokenUsage(prompt=100, completion=10)


async def test_llm_unavailable_propagates() -> None:
    llm = ScriptedLLM([LLMUnavailable("down")])
    with pytest.raises(LLMUnavailable):
        await (await make_use_case(llm)).execute(INQUIRY)


async def test_unknown_lead_propagates_without_llm_call() -> None:
    llm = ScriptedLLM([])
    with pytest.raises(LeadNotFound):
        await (await make_use_case(llm)).execute(Inquiry(lead_id="nope", message="?"))
    assert llm.calls == []
