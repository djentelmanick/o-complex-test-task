import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.application.chunking import SourceDocument, chunk_document, document_hash
from app.application.ports import Embedder, KnowledgeRepository
from app.domain.errors import EmbeddingDimensionMismatch

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestReport:
    indexed: int
    skipped: int
    deleted: int


class IngestKnowledgeUseCase:
    def __init__(
        self,
        knowledge: KnowledgeRepository,
        embedder: Embedder,
        *,
        embedding_model: str,
        embedding_dim: int,
        max_chunk_chars: int = 800,
    ) -> None:
        self._knowledge = knowledge
        self._embedder = embedder
        self._embedding_model = embedding_model
        self._embedding_dim = embedding_dim
        self._max_chunk_chars = max_chunk_chars

    async def execute(self, documents: Sequence[SourceDocument]) -> IngestReport:
        await self._check_stored_dimension()
        existing = await self._knowledge.document_hashes()
        indexed = skipped = 0
        for doc in documents:
            content_hash = document_hash(doc, self._embedding_model)
            if existing.get(doc.doc_id) == content_hash:
                skipped += 1
                continue
            chunks = chunk_document(doc, self._max_chunk_chars)
            vectors = await self._embedder.embed([c.content for c in chunks])
            self._check_dimensions(vectors)
            await self._knowledge.replace_document(doc.doc_id, content_hash, chunks, vectors)
            indexed += 1
            logger.info("indexed %s (%d chunks)", doc.doc_id, len(chunks))

        stale = sorted(set(existing) - {d.doc_id for d in documents})
        if stale:
            await self._knowledge.delete_documents(stale)
        return IngestReport(indexed=indexed, skipped=skipped, deleted=len(stale))

    async def _check_stored_dimension(self) -> None:
        stored = await self._knowledge.embedding_dimension()
        if stored is not None and stored != self._embedding_dim:
            raise EmbeddingDimensionMismatch(
                f"knowledge base stores vector({stored}), but EMBEDDING_DIM={self._embedding_dim}; "
                "recreate the database volume (docker compose down -v) after changing models"
            )

    def _check_dimensions(self, vectors: list[list[float]]) -> None:
        for vector in vectors:
            if len(vector) != self._embedding_dim:
                raise EmbeddingDimensionMismatch(
                    f"embedder returned {len(vector)}-dim vectors, "
                    f"but EMBEDDING_DIM={self._embedding_dim}"
                )
