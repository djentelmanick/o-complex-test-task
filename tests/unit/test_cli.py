from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.adapters.inbound import cli
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.application.ingest_knowledge import IngestKnowledgeUseCase
from app.config import Settings
from tests.fakes import InMemoryKnowledgeRepository


def test_ingest_command_loads_kb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("# Doc\n\n## Раздел\n\nтекст", encoding="utf-8")
    repo = InMemoryKnowledgeRepository()

    class FakeContainer:
        ingest_knowledge = IngestKnowledgeUseCase(
            repo, FakeEmbedder(8), embedding_model="fake", embedding_dim=8
        )

    @asynccontextmanager
    async def fake_build(_: Settings) -> AsyncIterator[FakeContainer]:
        yield FakeContainer()

    monkeypatch.setattr(cli, "build_container", fake_build)
    monkeypatch.setenv("APP_API_KEY", "test-api-key-0123456789abcdef")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("KB_DIR", str(tmp_path))

    assert cli.main(["ingest"]) == 0
    assert set(repo.docs) == {"doc"}


def test_unknown_command_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit):
        cli.main(["drop-everything"])
