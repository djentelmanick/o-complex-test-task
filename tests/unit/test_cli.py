from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.adapters.inbound import cli
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.application.ingest_knowledge import IngestKnowledgeUseCase
from app.config import Settings
from app.domain.models import Role
from tests.fakes import InMemoryKnowledgeRepository, InMemoryProcessedEvents


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


class FakeOAuth:
    def __init__(self) -> None:
        self.codes: list[str] = []

    async def exchange_code(self, code: str) -> None:
        self.codes.append(code)


class FakeGateway:
    def __init__(self) -> None:
        self.leads: list[str] = []
        self.messages: list[tuple[str, Role, str]] = []

    async def create_lead(self, name: str) -> str:
        self.leads.append(name)
        return str(len(self.leads))

    async def add_message(self, lead_id: str, role: Role, text: str) -> str:
        self.messages.append((lead_id, role, text))
        return f"note-{len(self.messages)}"


class FakeTools:
    def __init__(self) -> None:
        self.oauth = FakeOAuth()
        self.gateway = FakeGateway()
        self.processed_events = InMemoryProcessedEvents()


def patch_container(monkeypatch: pytest.MonkeyPatch, tools: FakeTools | None) -> None:
    class FakeContainer:
        amocrm = tools

    @asynccontextmanager
    async def fake_build(_: Settings) -> AsyncIterator[FakeContainer]:
        yield FakeContainer()

    monkeypatch.setattr(cli, "build_container", fake_build)
    monkeypatch.setenv("APP_API_KEY", "test-api-key-0123456789abcdef")
    monkeypatch.setenv("LLM_PROVIDER", "fake")


def test_amocrm_auth_exchanges_code(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = FakeTools()
    patch_container(monkeypatch, tools)
    assert cli.main(["amocrm-auth", "the-code"]) == 0
    assert tools.oauth.codes == ["the-code"]


def test_amocrm_say_adds_client_message(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = FakeTools()
    patch_container(monkeypatch, tools)
    assert cli.main(["amocrm-say", "123", "Как принимать?"]) == 0
    assert tools.gateway.messages == [("123", Role.CLIENT, "Как принимать?")]


def test_amocrm_seed_creates_demo_leads_with_history(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = FakeTools()
    patch_container(monkeypatch, tools)
    assert cli.main(["amocrm-seed"]) == 0
    assert len(tools.gateway.leads) == 3
    assert tools.gateway.messages[0][1] is Role.CLIENT
    assert len(tools.gateway.messages) == 9


def test_amocrm_commands_require_amocrm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_container(monkeypatch, None)
    assert cli.main(["amocrm-say", "1", "x"]) == 1


def test_amocrm_seed_does_not_trigger_the_assistant(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = FakeTools()
    patch_container(monkeypatch, tools)
    assert cli.main(["amocrm-seed"]) == 0
    assert tools.processed_events.keys == {f"crm:message:note-{i}" for i in range(1, 10)}
