import pytest

from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.application.chunking import SourceDocument
from app.application.ingest_knowledge import IngestKnowledgeUseCase, IngestReport
from app.domain.errors import EmbeddingDimensionMismatch
from tests.fakes import InMemoryKnowledgeRepository

DOCS = [
    SourceDocument("zeolite", "Zeolite", "## Приём\n\nКурс 15 дней"),
    SourceDocument("mineral", "Mineral", "## Польза\n\nЭнергия"),
]


class CountingEmbedder(FakeEmbedder):
    def __init__(self, dim: int) -> None:
        super().__init__(dim)
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return await super().embed(texts)


def make(
    repo: InMemoryKnowledgeRepository, embedder: FakeEmbedder, model: str = "fake"
) -> IngestKnowledgeUseCase:
    return IngestKnowledgeUseCase(repo, embedder, embedding_model=model, embedding_dim=16)


async def test_indexes_new_documents() -> None:
    repo = InMemoryKnowledgeRepository()
    report = await make(repo, FakeEmbedder(16)).execute(DOCS)
    assert report == IngestReport(indexed=2, skipped=0, deleted=0)
    assert await repo.count() == 2


async def test_unchanged_documents_are_skipped() -> None:
    repo = InMemoryKnowledgeRepository()
    await make(repo, FakeEmbedder(16)).execute(DOCS)
    embedder = CountingEmbedder(16)
    report = await make(repo, embedder).execute(DOCS)
    assert report == IngestReport(indexed=0, skipped=2, deleted=0)
    assert embedder.calls == 0


async def test_changed_document_is_reindexed() -> None:
    repo = InMemoryKnowledgeRepository()
    await make(repo, FakeEmbedder(16)).execute(DOCS)
    changed = [SourceDocument("zeolite", "Zeolite", "## Приём\n\nКурс 20 дней"), DOCS[1]]
    report = await make(repo, FakeEmbedder(16)).execute(changed)
    assert report == IngestReport(indexed=1, skipped=1, deleted=0)


async def test_switching_embedding_model_reindexes_everything() -> None:
    repo = InMemoryKnowledgeRepository()
    await make(repo, FakeEmbedder(16), model="fake").execute(DOCS)
    report = await make(repo, FakeEmbedder(16), model="Embeddings").execute(DOCS)
    assert report.indexed == 2


async def test_removed_documents_are_deleted() -> None:
    repo = InMemoryKnowledgeRepository()
    await make(repo, FakeEmbedder(16)).execute(DOCS)
    report = await make(repo, FakeEmbedder(16)).execute(DOCS[:1])
    assert report == IngestReport(indexed=0, skipped=1, deleted=1)
    assert set(await repo.document_hashes()) == {"zeolite"}


async def test_dimension_mismatch_fails_with_clear_error() -> None:
    repo = InMemoryKnowledgeRepository()
    use_case = IngestKnowledgeUseCase(
        repo, FakeEmbedder(8), embedding_model="fake", embedding_dim=16
    )
    with pytest.raises(EmbeddingDimensionMismatch, match="EMBEDDING_DIM"):
        await use_case.execute(DOCS)
    assert await repo.count() == 0
