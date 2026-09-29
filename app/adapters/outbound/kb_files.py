from pathlib import Path

from app.application.chunking import SourceDocument, parse_markdown


def load_markdown_documents(directory: Path) -> list[SourceDocument]:
    return [
        parse_markdown(path.stem, path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.md"))
    ]
