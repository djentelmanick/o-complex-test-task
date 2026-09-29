import asyncio
from collections.abc import Iterable, Iterator

import numpy as np

from app.adapters.outbound.local.embedder import LocalEmbedder


class StubModel:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, documents: Iterable[str]) -> Iterator[np.ndarray]:
        batch = list(documents)
        self.calls.append(batch)
        for i, _ in enumerate(batch):
            yield np.array([float(i), 0.5], dtype=np.float32)


async def test_returns_plain_float_lists_in_input_order() -> None:
    model = StubModel()
    embedder = LocalEmbedder(model_factory=lambda: model)
    vectors = await embedder.embed(["a", "b"])
    assert vectors == [[0.0, 0.5], [1.0, 0.5]]
    assert all(type(x) is float for v in vectors for x in v)
    assert model.calls == [["a", "b"]]


async def test_model_is_loaded_lazily_and_once_under_concurrency() -> None:
    created: list[StubModel] = []

    def factory() -> StubModel:
        created.append(StubModel())
        return created[-1]

    embedder = LocalEmbedder(model_factory=factory)
    assert created == []
    await asyncio.gather(*(embedder.embed(["x"]) for _ in range(5)))
    assert len(created) == 1
