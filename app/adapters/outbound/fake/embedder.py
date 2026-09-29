import hashlib
import math
import re

_WORD = re.compile(r"\w+", re.UNICODE)
# Грубый стемминг: у русских слов окончания меняются, первые 5 букв дают устойчивый ключ
_STEM_LENGTH = 5


class FakeEmbedder:
    """Детерминированный bag-of-words эмбеддер для тестов и запуска без GigaChat."""

    def __init__(self, dim: int) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for word in _WORD.findall(text.lower()):
            digest = hashlib.sha256(word[:_STEM_LENGTH].encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % self._dim] += 1.0
        if not any(vector):
            vector[0] = 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector]
