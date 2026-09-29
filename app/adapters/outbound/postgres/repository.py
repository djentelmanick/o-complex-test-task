from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.outbound.postgres.models import KnowledgeChunkRow
from app.domain.errors import KnowledgeBaseUnavailable
from app.domain.models import KnowledgeChunk, RetrievedChunk


class PgVectorKnowledgeRepository:
    """Хранилище фрагментов в pgvector.

    Все чтения видят только векторы текущей модели эмбеддингов: после смены модели или
    прерванной переиндексации в поиск не попадут векторы из другого пространства.
    """

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], *, embedding_model: str
    ) -> None:
        self._sessionmaker = sessionmaker
        self._embedding_model = embedding_model

    async def search(
        self, vector: list[float], limit: int, min_score: float
    ) -> list[RetrievedChunk]:
        distance = KnowledgeChunkRow.embedding.cosine_distance(vector)
        stmt = (
            select(
                KnowledgeChunkRow.id,
                KnowledgeChunkRow.doc_id,
                KnowledgeChunkRow.title,
                KnowledgeChunkRow.content,
                (1 - distance).label("score"),
            )
            .where(KnowledgeChunkRow.embedding_model == self._embedding_model)
            .where(distance <= 1 - min_score)
            .order_by(distance)
            .limit(limit)
        )
        try:
            async with self._sessionmaker() as session:
                rows = (await session.execute(stmt)).all()
        except DBAPIError as exc:
            # Например, размерность вектора запроса не совпадает с колонкой
            raise KnowledgeBaseUnavailable(str(exc.orig)) from exc
        return [
            RetrievedChunk(
                chunk=KnowledgeChunk(id=r.id, doc_id=r.doc_id, title=r.title, content=r.content),
                score=float(r.score),
            )
            for r in rows
        ]

    async def replace_document(
        self,
        doc_id: str,
        content_hash: str,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                delete(KnowledgeChunkRow).where(KnowledgeChunkRow.doc_id == doc_id)
            )
            session.add_all(
                KnowledgeChunkRow(
                    id=chunk.id,
                    doc_id=doc_id,
                    title=chunk.title,
                    content=chunk.content,
                    content_hash=content_hash,
                    embedding_model=self._embedding_model,
                    embedding=embedding,
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            )

    async def delete_documents(self, doc_ids: list[str]) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                delete(KnowledgeChunkRow).where(KnowledgeChunkRow.doc_id.in_(doc_ids))
            )

    async def document_hashes(self) -> dict[str, str]:
        stmt = (
            select(KnowledgeChunkRow.doc_id, KnowledgeChunkRow.content_hash)
            .where(KnowledgeChunkRow.embedding_model == self._embedding_model)
            .distinct()
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).all()
        return {doc_id: content_hash for doc_id, content_hash in rows}

    async def count(self) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeChunkRow)
            .where(KnowledgeChunkRow.embedding_model == self._embedding_model)
        )
        async with self._sessionmaker() as session:
            total = await session.scalar(stmt)
        return int(total or 0)

    async def embedding_dimension(self) -> int | None:
        # Для типа vector(N) PostgreSQL хранит N в atttypmod колонки
        stmt = text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'knowledge_chunks'::regclass AND attname = 'embedding'"
        )
        async with self._sessionmaker() as session:
            dimension = await session.scalar(stmt)
        return int(dimension) if dimension is not None and dimension > 0 else None
