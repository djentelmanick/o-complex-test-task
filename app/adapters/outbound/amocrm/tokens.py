from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_at: float


class TokenStore(Protocol):
    async def load(self) -> TokenPair | None: ...

    async def save(self, pair: TokenPair) -> None: ...
