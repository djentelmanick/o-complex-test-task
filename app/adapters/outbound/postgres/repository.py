from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.outbound.postgres.models import KnowledgeChunkRow
from app.domain.models import KnowledgeChunk, RetrievedChunk


class PgVectorKnowledgeRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

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
            .where(distance <= 1 - min_score)
            .order_by(distance)
            .limit(limit)
        )
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).all()
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
        stmt = select(KnowledgeChunkRow.doc_id, KnowledgeChunkRow.content_hash).distinct()
        async with self._sessionmaker() as session:
            rows = (await session.execute(stmt)).all()
        return {doc_id: content_hash for doc_id, content_hash in rows}

    async def count(self) -> int:
        async with self._sessionmaker() as session:
            total = await session.scalar(select(func.count()).select_from(KnowledgeChunkRow))
        return int(total or 0)
