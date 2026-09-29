import hashlib
from dataclasses import dataclass

from app.domain.models import KnowledgeChunk


@dataclass(frozen=True, slots=True)
class SourceDocument:
    doc_id: str
    title: str
    body: str


def parse_markdown(doc_id: str, text: str) -> SourceDocument:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        return SourceDocument(doc_id, lines[0][2:].strip(), "\n".join(lines[1:]).strip())
    return SourceDocument(doc_id, doc_id, text.strip())


def chunk_document(doc: SourceDocument, max_chars: int = 800) -> list[KnowledgeChunk]:
    pieces = [p for section in _split_sections(doc.body) for p in _split_long(section, max_chars)]
    # Заголовок статьи в каждом чанке: иначе фрагмент «## Как принимать»
    # теряет, о каком продукте речь
    return [
        KnowledgeChunk(
            id=f"{doc.doc_id}#{i}",
            doc_id=doc.doc_id,
            title=doc.title,
            content=f"{doc.title}\n{piece}",
        )
        for i, piece in enumerate(pieces)
    ]


def document_hash(doc: SourceDocument, embedding_model: str) -> str:
    payload = "\n".join((embedding_model, doc.title, doc.body))
    return hashlib.sha256(payload.encode()).hexdigest()


def _split_sections(body: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.startswith("## ") and current:
            sections.append("\n".join(current).strip())
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [s for s in sections if s]


def _split_long(section: str, max_chars: int) -> list[str]:
    if len(section) <= max_chars:
        return [section]
    pieces: list[str] = []
    current = ""
    for paragraph in section.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            pieces.append(current)
            current = paragraph
    if current:
        pieces.append(current)
    return pieces
