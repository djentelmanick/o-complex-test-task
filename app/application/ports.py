from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.models import KnowledgeChunk, Lead, RetrievedChunk, TokenUsage


@dataclass(frozen=True, slots=True)
class LLMResult:
    arguments: dict[str, Any]
    usage: TokenUsage


class CRMGateway(Protocol):
    async def get_lead(self, lead_id: str) -> Lead: ...

    async def list_leads(self) -> list[Lead]: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class KnowledgeRepository(Protocol):
    async def search(
        self, vector: list[float], limit: int, min_score: float
    ) -> list[RetrievedChunk]: ...

    async def replace_document(
        self,
        doc_id: str,
        content_hash: str,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None: ...

    async def delete_documents(self, doc_ids: list[str]) -> None: ...

    async def document_hashes(self) -> dict[str, str]: ...

    async def count(self) -> int: ...


class LLMClient(Protocol):
    async def complete_structured(
        self, system: str, user: str, function_name: str, schema: dict[str, Any]
    ) -> LLMResult: ...
