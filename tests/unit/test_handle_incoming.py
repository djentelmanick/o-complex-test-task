from app.adapters.outbound.crm.mock import MockCRMGateway
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.application.answer_inquiry import AnswerInquiryUseCase
from app.application.handle_incoming import (
    MAX_MESSAGE_LENGTH,
    HandleIncomingMessageUseCase,
    HandleResult,
)
from app.application.ports import LLMResult
from app.domain.errors import LLMUnavailable
from app.domain.models import DialogMessage, Lead, Role, TokenUsage
from tests.fakes import (
    InMemoryKnowledgeRepository,
    InMemoryProcessedEvents,
    RecordingPublisher,
    ScriptedLLM,
)

LEAD = Lead(
    id="100",
    name="Марина",
    dialog=(
        DialogMessage(Role.CLIENT, "Дороговато для меня", id="1"),
        DialogMessage(Role.MANAGER, "Есть формат поменьше", id="2"),
        DialogMessage(Role.CLIENT, "Как принимать цеолит?", id="3"),
        DialogMessage(Role.CLIENT, "   ", id="4"),
        DialogMessage(Role.CLIENT, "я" * (MAX_MESSAGE_LENGTH + 500), id="5"),
    ),
)
OK = LLMResult(
    arguments={"client_reply": "Здравствуйте!", "manager_hint": "Mini", "used_chunk_ids": []},
    usage=TokenUsage(prompt=10, completion=5),
)


def make(llm: ScriptedLLM) -> tuple[HandleIncomingMessageUseCase, RecordingPublisher]:
    crm = MockCRMGateway([LEAD])
    answer_inquiry = AnswerInquiryUseCase(
        crm, FakeEmbedder(dim=32), InMemoryKnowledgeRepository(), llm, min_score=0.0
    )
    publisher = RecordingPublisher()
    use_case = HandleIncomingMessageUseCase(
        crm=crm,
        answer_inquiry=answer_inquiry,
        publisher=publisher,
        processed_events=InMemoryProcessedEvents(),
    )
    return use_case, publisher


def dialog_block(prompt: str) -> str:
    return prompt.split("<dialog>")[1].split("</dialog>")[0]


async def test_client_message_is_answered_with_history_before_it() -> None:
    llm = ScriptedLLM([OK])
    use_case, publisher = make(llm)

    result = await use_case.execute("100", "3")

    assert result is HandleResult.PROCESSED
    assert [lead_id for lead_id, _ in publisher.published] == ["100"]
    _, prompt = llm.calls[0]
    assert "Дороговато для меня" in dialog_block(prompt)
    assert "Как принимать цеолит?" not in dialog_block(prompt)
    assert prompt.count("Как принимать цеолит?") == 1


async def test_duplicate_delivery_is_processed_once() -> None:
    llm = ScriptedLLM([OK])
    use_case, publisher = make(llm)
    assert await use_case.execute("100", "3") is HandleResult.PROCESSED
    assert await use_case.execute("100", "3") is HandleResult.DUPLICATE
    assert len(llm.calls) == 1
    assert len(publisher.published) == 1


async def test_manager_message_does_not_trigger() -> None:
    llm = ScriptedLLM([])
    use_case, publisher = make(llm)
    assert await use_case.execute("100", "2") is HandleResult.IGNORED
    assert llm.calls == []
    assert publisher.published == []


async def test_unknown_message_is_ignored() -> None:
    use_case, _ = make(ScriptedLLM([]))
    assert await use_case.execute("100", "999") is HandleResult.IGNORED


async def test_deleted_lead_is_ignored() -> None:
    use_case, _ = make(ScriptedLLM([]))
    assert await use_case.execute("404", "3") is HandleResult.IGNORED


async def test_blank_message_is_ignored_without_llm_call() -> None:
    llm = ScriptedLLM([])
    use_case, _ = make(llm)
    assert await use_case.execute("100", "4") is HandleResult.IGNORED
    assert llm.calls == []


async def test_long_message_is_truncated() -> None:
    llm = ScriptedLLM([OK])
    use_case, _ = make(llm)
    await use_case.execute("100", "5")
    _, prompt = llm.calls[0]
    assert "я" * MAX_MESSAGE_LENGTH in prompt
    assert "я" * (MAX_MESSAGE_LENGTH + 1) not in prompt


async def test_llm_outage_publishes_unavailable_note() -> None:
    use_case, publisher = make(ScriptedLLM([LLMUnavailable("down")]))
    assert await use_case.execute("100", "3") is HandleResult.UNAVAILABLE
    assert publisher.unavailable == ["100"]
    assert publisher.published == []
