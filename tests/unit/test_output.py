import pytest

from app.application.output import (
    CLIENT_REPLY_MAX,
    FALLBACK_CLIENT_REPLY,
    fallback_answer,
    parse_answer,
    select_sources,
)
from app.domain.errors import LLMInvalidOutput
from app.domain.models import KnowledgeChunk, RetrievedChunk, Source, TokenUsage

USAGE = TokenUsage(prompt=100, completion=20)


def retrieved(chunk_id: str, score: float) -> RetrievedChunk:
    doc_id = chunk_id.split("#")[0]
    return RetrievedChunk(KnowledgeChunk(chunk_id, doc_id, doc_id.title(), "text"), score)


RETRIEVED = [
    retrieved("zeolite#0", 0.9),
    retrieved("zeolite#1", 0.8),
    retrieved("mineral#0", 0.7),
    retrieved("safety#0", 0.6),
]


def valid_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "client_reply": "  Здравствуйте!  ",
        "manager_hint": "Предложите Mineral Complex",
        "used_chunk_ids": ["mineral#0"],
    }
    args.update(overrides)
    return args


def test_parse_valid_answer_strips_and_maps_sources() -> None:
    answer = parse_answer(valid_args(extra_field="ignored"), RETRIEVED, USAGE)
    assert answer.client_reply == "Здравствуйте!"
    assert answer.sources == (Source("mineral", "Mineral"),)
    assert answer.usage == USAGE
    assert answer.fallback is False


@pytest.mark.parametrize(
    "args",
    [
        valid_args(client_reply="x" * (CLIENT_REPLY_MAX + 1)),
        valid_args(client_reply="   "),
        valid_args(manager_hint=None),
        {"client_reply": "ok"},
        valid_args(used_chunk_ids="mineral#0; zeolite#0"),
    ],
)
def test_invalid_answers_raise(args: dict[str, object]) -> None:
    with pytest.raises(LLMInvalidOutput):
        parse_answer(args, RETRIEVED, USAGE)


def test_hallucinated_ids_are_dropped_and_docs_deduplicated() -> None:
    sources = select_sources(["zeolite#1", "made-up#7", "zeolite#0", "safety#0"], RETRIEVED)
    assert sources == (Source("zeolite", "Zeolite"), Source("safety", "Safety"))


def test_without_valid_ids_top_two_documents_by_score_are_used() -> None:
    sources = select_sources(["made-up#1"], list(reversed(RETRIEVED)))
    assert sources == (Source("zeolite", "Zeolite"), Source("mineral", "Mineral"))


def test_without_retrieved_chunks_sources_are_empty() -> None:
    assert select_sources(["zeolite#0"], []) == ()


def test_fallback_answer() -> None:
    answer = fallback_answer(USAGE)
    assert answer.fallback is True
    assert answer.client_reply == FALLBACK_CLIENT_REPLY
    assert answer.sources == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Поддержит ваше здоровье.,", "Поддержит ваше здоровье."),
        ('Начать с небольшой упаковки.\n",', "Начать с небольшой упаковки."),
        ('Выберите "Zeolite Max"', 'Выберите "Zeolite Max"'),
        ("Мы ценим честность! ‹/client_reply›,", "Мы ценим честность!"),
        ("для полноценного курса,", "для полноценного курса"),
        (
            "уточните у менеджера более подробно.formating=markdown",
            "уточните у менеджера более подробно.",
        ),
        ("Ответ.\nformatting=markdown", "Ответ."),
        ("Спасибо!\\n\\nС уважением\\n", "Спасибо!\n\nС уважением"),
        ("Без артефактов!", "Без артефактов!"),
    ],
)
def test_trailing_json_artifacts_are_removed(raw: str, expected: str) -> None:
    answer = parse_answer(valid_args(client_reply=raw, manager_hint=raw), RETRIEVED, USAGE)
    assert answer.client_reply == expected
    assert answer.manager_hint == expected
