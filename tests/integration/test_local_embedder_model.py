import math

import pytest

from app.adapters.outbound.local.embedder import LocalEmbedder
from app.config import Settings

pytestmark = pytest.mark.integration


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def test_baked_model_ranks_semantically_related_text_higher() -> None:
    settings = Settings(_env_file=None, app_api_key="x" * 16, llm_provider="fake")  # type: ignore[arg-type]
    embedder = LocalEmbedder.from_settings(settings)
    query, related, unrelated = await embedder.embed(
        [
            "Можно ли принимать при беременности?",
            "При беременности и кормлении грудью нужна консультация врача.",
            "Доставляем по России, сроки уточняет менеджер.",
        ]
    )
    assert len(query) == settings.embedding_dim
    assert cosine(query, related) > cosine(query, unrelated)
