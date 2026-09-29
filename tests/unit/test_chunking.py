from pathlib import Path

from app.adapters.outbound.kb_files import load_markdown_documents
from app.application.chunking import (
    SourceDocument,
    chunk_document,
    document_hash,
    parse_markdown,
)

MD = """# Zeolite Standard

Вводный абзац.

## Как принимать

Курс 15 дней, затем 5 дней перерыв.

## Состав

Природный цеолит.
"""


def test_parse_markdown_extracts_title() -> None:
    doc = parse_markdown("zeolite-standard", MD)
    assert doc.title == "Zeolite Standard"
    assert doc.body.startswith("Вводный абзац.")


def test_parse_markdown_without_heading_uses_doc_id() -> None:
    assert parse_markdown("notes", "просто текст").title == "notes"


def test_chunks_split_by_sections_and_prefixed_with_title() -> None:
    chunks = chunk_document(parse_markdown("zeolite-standard", MD))
    assert [c.id for c in chunks] == [
        "zeolite-standard#0",
        "zeolite-standard#1",
        "zeolite-standard#2",
    ]
    assert all(c.content.startswith("Zeolite Standard\n") for c in chunks)
    assert "## Как принимать" in chunks[1].content
    assert chunks[1].title == "Zeolite Standard"


def test_long_section_is_split_by_paragraphs() -> None:
    body = "## Раздел\n\n" + "\n\n".join("абзац " + "x" * 300 for _ in range(4))
    chunks = chunk_document(SourceDocument("doc", "Doc", body), max_chars=800)
    assert len(chunks) >= 2
    assert all(len(c.content) <= 800 + len("Doc\n") for c in chunks)


def test_hash_depends_on_content_and_embedding_model() -> None:
    doc = SourceDocument("d", "T", "body")
    assert document_hash(doc, "Embeddings") == document_hash(doc, "Embeddings")
    assert document_hash(doc, "Embeddings") != document_hash(doc, "fake")
    assert document_hash(doc, "Embeddings") != document_hash(
        SourceDocument("d", "T", "body2"), "Embeddings"
    )


def test_load_markdown_documents(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("# B\n\nтекст", encoding="utf-8")
    (tmp_path / "a.md").write_text("# A\n\nтекст", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("не markdown", encoding="utf-8")
    docs = load_markdown_documents(tmp_path)
    assert [d.doc_id for d in docs] == ["a", "b"]
