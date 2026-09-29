import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.outbound.postgres.repository import PgVectorKnowledgeRepository
from tests.fakes import make_chunk

pytestmark = pytest.mark.integration

TEST_DIM = "3"


async def alembic(monkeypatch: pytest.MonkeyPatch, action: str, revision: str) -> None:
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("EMBEDDING_DIM", TEST_DIM)
    # env.py сам вызывает asyncio.run, поэтому из async-теста запускаем его в отдельном потоке
    await asyncio.to_thread(getattr(command, action), Config(toml_file="pyproject.toml"), revision)


async def table_exists(engine_url: str) -> bool:
    engine = create_async_engine(engine_url)
    async with engine.connect() as conn:
        exists = await conn.scalar(text("SELECT to_regclass('knowledge_chunks') IS NOT NULL"))
    await engine.dispose()
    return bool(exists)


@pytest.fixture
async def repo(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[PgVectorKnowledgeRepository]:
    # Схему создаёт миграция, а не create_all: тесты заодно проверяют, что миграция рабочая
    await alembic(monkeypatch, "downgrade", "base")
    await alembic(monkeypatch, "upgrade", "head")
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield PgVectorKnowledgeRepository(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def test_migrations_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ["TEST_DATABASE_URL"]
    await alembic(monkeypatch, "upgrade", "head")
    assert await table_exists(url)
    await alembic(monkeypatch, "downgrade", "base")
    assert not await table_exists(url)
    await alembic(monkeypatch, "upgrade", "head")
    assert await table_exists(url)


async def test_migration_creates_hnsw_index(repo: PgVectorKnowledgeRepository) -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with engine.connect() as conn:
        indexdef = await conn.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_knowledge_chunks_embedding'"
            )
        )
    await engine.dispose()
    assert indexdef is not None
    assert "hnsw" in indexdef
    assert "vector_cosine_ops" in indexdef


async def test_search_orders_by_cosine_similarity(repo: PgVectorKnowledgeRepository) -> None:
    await repo.replace_document("a", "h1", [make_chunk("a", 0, "A")], [[1.0, 0.0, 0.0]])
    await repo.replace_document("b", "h2", [make_chunk("b", 0, "B")], [[0.7, 0.7, 0.0]])
    await repo.replace_document("c", "h3", [make_chunk("c", 0, "C")], [[0.0, 0.0, 1.0]])

    hits = await repo.search([1.0, 0.1, 0.0], limit=2, min_score=0.0)

    assert [h.chunk.doc_id for h in hits] == ["a", "b"]
    assert hits[0].score > hits[1].score
    assert hits[0].chunk.content == "A"
    assert hits[0].chunk.title == "A"


async def test_min_score_filters_irrelevant_chunks(repo: PgVectorKnowledgeRepository) -> None:
    await repo.replace_document("a", "h1", [make_chunk("a")], [[1.0, 0.0, 0.0]])
    await repo.replace_document("c", "h3", [make_chunk("c")], [[0.0, 0.0, 1.0]])
    hits = await repo.search([1.0, 0.0, 0.0], limit=4, min_score=0.5)
    assert [h.chunk.doc_id for h in hits] == ["a"]


async def test_replace_document_swaps_chunks_and_hash(repo: PgVectorKnowledgeRepository) -> None:
    await repo.replace_document(
        "a", "h1", [make_chunk("a", 0), make_chunk("a", 1)], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    )
    await repo.replace_document("a", "h2", [make_chunk("a", 0)], [[1.0, 0.0, 0.0]])
    assert await repo.count() == 1
    assert await repo.document_hashes() == {"a": "h2"}


async def test_delete_documents(repo: PgVectorKnowledgeRepository) -> None:
    await repo.replace_document("a", "h1", [make_chunk("a")], [[1.0, 0.0, 0.0]])
    await repo.replace_document("b", "h2", [make_chunk("b")], [[0.0, 1.0, 0.0]])
    await repo.delete_documents(["a"])
    assert await repo.document_hashes() == {"b": "h2"}
