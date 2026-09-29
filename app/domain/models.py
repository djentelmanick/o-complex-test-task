from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    CLIENT = "client"
    MANAGER = "manager"


@dataclass(frozen=True, slots=True)
class DialogMessage:
    role: Role
    text: str


@dataclass(frozen=True, slots=True)
class Lead:
    id: str
    name: str
    dialog: tuple[DialogMessage, ...]


@dataclass(frozen=True, slots=True)
class Inquiry:
    lead_id: str
    message: str


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    id: str
    doc_id: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: KnowledgeChunk
    score: float


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(self.prompt + other.prompt, self.completion + other.completion)


@dataclass(frozen=True, slots=True)
class Source:
    doc_id: str
    title: str


@dataclass(frozen=True, slots=True)
class AssistantAnswer:
    client_reply: str
    manager_hint: str
    sources: tuple[Source, ...]
    usage: TokenUsage
    fallback: bool = False
