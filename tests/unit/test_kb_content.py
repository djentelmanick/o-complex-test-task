import re
from pathlib import Path

from app.adapters.outbound.kb_files import load_markdown_documents
from app.application.chunking import chunk_document

KB_DIR = Path("kb")


def test_kb_has_all_articles() -> None:
    doc_ids = {d.doc_id for d in load_markdown_documents(KB_DIR)}
    assert {
        "company",
        "zeolite-mini",
        "zeolite-standard",
        "zeolite-max",
        "mineral-complex",
        "combinations",
        "safety",
        "delivery-payment",
        "objections",
    } <= doc_ids


def test_kb_contains_no_prices() -> None:
    price = re.compile(r"\d[\d\s]*(₽|руб)", re.IGNORECASE)
    for doc in load_markdown_documents(KB_DIR):
        assert not price.search(doc.body), doc.doc_id


def test_every_article_produces_chunks_with_title() -> None:
    for doc in load_markdown_documents(KB_DIR):
        chunks = chunk_document(doc)
        assert chunks, doc.doc_id
        assert doc.title != doc.doc_id, f"{doc.doc_id} has no '# ' heading"
