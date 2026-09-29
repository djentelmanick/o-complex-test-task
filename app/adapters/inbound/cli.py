import argparse
import asyncio
import logging
import sys

from app.adapters.outbound.kb_files import load_markdown_documents
from app.bootstrap import build_container
from app.config import Settings
from app.domain.errors import DomainError

logger = logging.getLogger("app.cli")


async def _ingest(settings: Settings) -> None:
    documents = load_markdown_documents(settings.kb_dir)
    async with build_container(settings) as container:
        report = await container.ingest_knowledge.execute(documents)
    logger.info(
        "knowledge base ingested: indexed=%d skipped=%d deleted=%d",
        report.indexed,
        report.skipped,
        report.deleted,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inquiry-assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="load kb/*.md into the vector store")
    parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(_ingest(Settings()))
    except DomainError as exc:
        logger.error("ingest failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
