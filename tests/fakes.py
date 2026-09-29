import math
from collections.abc import Sequence
from typing import Any

from app.application.ports import Embedder, LLMResult
from app.domain.models import AssistantAnswer, KnowledgeChunk, RetrievedChunk


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class InMemoryKnowledgeRepository:
    def __init__(self, dimension: int | None = None) -> None:
        self.docs: dict[str, tuple[str, list[tuple[KnowledgeChunk, list[float]]]]] = {}
        self.dimension = dimension

    async def embedding_dimension(self) -> int | None:
        return self.dimension

    async def search(
        self, vector: list[float], limit: int, min_score: float
    ) -> list[RetrievedChunk]:
        scored = [
            RetrievedChunk(chunk, _cosine(vector, emb))
            for _, items in self.docs.values()
            for chunk, emb in items
        ]
        hits = [r for r in scored if r.score >= min_score]
        return sorted(hits, key=lambda r: r.score, reverse=True)[:limit]

    async def replace_document(
        self,
        doc_id: str,
        content_hash: str,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        self.docs[doc_id] = (content_hash, list(zip(chunks, embeddings, strict=True)))

    async def delete_documents(self, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            self.docs.pop(doc_id, None)

    async def document_hashes(self) -> dict[str, str]:
        return {doc_id: h for doc_id, (h, _) in self.docs.items()}

    async def count(self) -> int:
        return sum(len(items) for _, items in self.docs.values())


class ScriptedLLM:
    def __init__(self, responses: list[LLMResult | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete_structured(
        self, system: str, user: str, function_name: str, schema: dict[str, Any]
    ) -> LLMResult:
        self.calls.append((system, user))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_chunk(doc_id: str, n: int = 0, content: str = "...") -> KnowledgeChunk:
    return KnowledgeChunk(id=f"{doc_id}#{n}", doc_id=doc_id, title=doc_id.title(), content=content)


async def seed(
    repo: InMemoryKnowledgeRepository, embedder: Embedder, chunks: list[KnowledgeChunk]
) -> None:
    by_doc: dict[str, list[KnowledgeChunk]] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.doc_id, []).append(chunk)
    for doc_id, doc_chunks in by_doc.items():
        vectors = await embedder.embed([c.content for c in doc_chunks])
        await repo.replace_document(doc_id, "hash", doc_chunks, vectors)


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, AssistantAnswer]] = []
        self.unavailable: list[str] = []

    async def publish(self, lead_id: str, answer: AssistantAnswer) -> None:
        self.published.append((lead_id, answer))

    async def publish_unavailable(self, lead_id: str) -> None:
        self.unavailable.append(lead_id)


class InMemoryProcessedEvents:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def first_seen(self, key: str) -> bool:
        if key in self.keys:
            return False
        self.keys.add(key)
        return True
