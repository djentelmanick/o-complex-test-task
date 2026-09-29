import math

from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.adapters.outbound.fake.llm import FakeLLM
from app.application.output import ANSWER_SCHEMA, FUNCTION_NAME, parse_answer
from app.application.prompts import build_user_prompt
from app.domain.models import RetrievedChunk
from tests.fakes import InMemoryKnowledgeRepository, make_chunk, seed


async def test_fake_embedder_is_deterministic_normalized_and_sized() -> None:
    embedder = FakeEmbedder(dim=64)
    [a, b] = await embedder.embed(["Цеолит для детокса", "Цеолит для детокса"])
    assert a == b
    assert len(a) == 64
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)


async def test_fake_embedder_handles_text_without_words() -> None:
    [vector] = await FakeEmbedder(dim=8).embed(["???"])
    assert any(vector)


async def test_fake_embedder_ranks_related_text_higher() -> None:
    embedder = FakeEmbedder(dim=256)
    repo = InMemoryKnowledgeRepository()
    await seed(
        repo,
        embedder,
        [
            make_chunk("zeolite", content="Цеолит сорбент для детокса и очищения"),
            make_chunk("delivery", content="Доставка курьером и оплата картой"),
        ],
    )
    [query] = await embedder.embed(["как принимать цеолит для очищения"])
    hits = await repo.search(query, limit=1, min_score=0.0)
    assert hits[0].chunk.doc_id == "zeolite"


async def test_fake_llm_returns_valid_answer_citing_prompt_chunks() -> None:
    chunk = RetrievedChunk(make_chunk("zeolite"), 0.9)
    user = build_user_prompt("вопрос", [], [chunk])
    result = await FakeLLM().complete_structured("system", user, FUNCTION_NAME, ANSWER_SCHEMA)
    answer = parse_answer(result.arguments, [chunk], result.usage)
    assert answer.sources[0].doc_id == "zeolite"
    assert result.usage.total > 0
