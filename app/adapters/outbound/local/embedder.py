import asyncio
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from app.config import Settings


class _EmbeddingModel(Protocol):
    def embed(self, documents: Iterable[str]) -> Iterable[Any]: ...


class LocalEmbedder:
    """Эмбеддинги локальной ONNX-моделью (fastembed): без сети и платных API."""

    def __init__(self, model_factory: Callable[[], _EmbeddingModel]) -> None:
        self._model_factory = model_factory
        self._model: _EmbeddingModel | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "LocalEmbedder":
        def factory() -> _EmbeddingModel:
            # Импорт здесь: onnxruntime грузится долго и не нужен, если провайдер другой
            from fastembed import TextEmbedding

            cache_dir = str(settings.embedding_cache_dir) if settings.embedding_cache_dir else None
            return TextEmbedding(settings.local_embedding_model, cache_dir=cache_dir)

        return cls(factory)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = await self._get_model()
        # Инференс CPU-bound: уводим из event loop, чтобы не блокировать другие запросы
        vectors = await asyncio.to_thread(lambda: list(model.embed(texts)))
        return [[float(x) for x in vector] for vector in vectors]

    async def _get_model(self) -> _EmbeddingModel:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    self._model = await asyncio.to_thread(self._model_factory)
        return self._model
