# AmoCRM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ассистент работает внутри AmoCRM: входящее сообщение клиента (`sms_in`) в сделке → вебхук через cloudflared-туннель → ответ и подсказка по допродаже записываются примечанием `service_message` в ту же сделку.

**Architecture:** Новые порты `AnswerPublisher` и `ProcessedEvents`, use case `HandleIncomingMessageUseCase` поверх существующего `AnswerInquiryUseCase.answer(...)`. Адаптеры AmoCRM (OAuth с ротацией токенов в Postgres, HTTP-клиент, gateway, регистрация вебхука) в `app/adapters/outbound/amocrm/`. Inbound: вебхук-роут с фоновыми задачами, `TunnelGuardMiddleware`, CLI-команды. Переключение `CRM_PROVIDER=mock|amocrm`.

**Tech Stack:** Python 3.12+, FastAPI, httpx, SQLAlchemy async + Alembic, `cryptography` (Fernet), respx, cloudflared (quick tunnel) в Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-29-amocrm-integration-design.md`

## Global Constraints

- Код, идентификаторы и коммиты на английском; тексты для людей на русском; комментарии только «почему», на русском.
- Conventional Commits; перед каждым коммитом зелёные `poetry run pytest -q`, `poetry run ruff check .`, `poetry run ruff format --check .`, `poetry run mypy app`.
- Триггер ассистента — только примечания `sms_in`; результат — `service_message` с `params.service = "Ассистент O-complex"`.
- Роли диалога: `sms_in` → `client`, `sms_out` → `manager`; прочие типы примечаний в диалог не входят.
- Путь вебхука: `/integrations/amocrm/webhook/{secret}`; `AMOCRM_WEBHOOK_SECRET` ≥ 32 символов; сравнение через `secrets.compare_digest`.
- Токены AmoCRM хранятся только в Postgres, зашифрованные Fernet (`AMOCRM_TOKEN_KEY`); секреты в настройках — `SecretStr`.
- `AMOCRM_SUBDOMAIN` — только `^[a-z0-9-]{1,63}$` (подставляется в URL).
- Сообщение клиента из AmoCRM обрезается до 2000 символов, как в HTTP API.
- `CRM_PROVIDER=mock` (по умолчанию) — поведение без изменений; все существующие тесты остаются зелёными.
- Интеграционные тесты (`-m integration`) запускаются только в Docker: `make test-integration`.

## Review Focus

1. Один POST вебхука содержит несколько примечаний (AmoCRM батчит события) → обрабатывается каждое. Тест — задача 7.
2. Пустой текст `sms_in` (только пробелы) → `ignored`, LLM не вызывается. Тест — задача 2.
3. Сообщение клиента длиннее 2000 символов (вебхук обходит валидацию HTTP API) → в LLM уходит ровно 2000. Тест — задача 2.
4. Сделку удалили между вебхуком и обработкой → `ignored`, фоновая задача не падает. Тест — задача 2.
5. Сообщение клиента не должно дублироваться в промпте (и в `<dialog>`, и в `<client_message>`). Тест — задача 2.

---

## File Structure

```
app/config.py                                   + CRM_PROVIDER, AMOCRM_* настройки
app/domain/errors.py                            + CRMUnavailable
app/domain/models.py                            DialogMessage.id
app/application/ports.py                        + AnswerPublisher, ProcessedEvents
app/application/answer_inquiry.py               + answer(message, dialog)
app/application/handle_incoming.py              HandleIncomingMessageUseCase, HandleResult
app/adapters/outbound/amocrm/__init__.py
app/adapters/outbound/amocrm/errors.py          AmoCRMAuthError, AmoCRMNotFound
app/adapters/outbound/amocrm/tokens.py          TokenPair, TokenStore (Protocol)
app/adapters/outbound/amocrm/token_store.py     PgTokenStore (Fernet)
app/adapters/outbound/amocrm/oauth.py           AmoCRMOAuth
app/adapters/outbound/amocrm/client.py          AmoCRMClient
app/adapters/outbound/amocrm/gateway.py         AmoCRMGateway, format_answer_note
app/adapters/outbound/amocrm/webhooks.py        AmoCRMWebhookRegistrar, resolve_tunnel_url, webhook_destination
app/adapters/outbound/amocrm/tools.py           AmoCRMTools (набор для CLI/lifespan)
app/adapters/outbound/postgres/models.py        + AmoCRMTokenRow, ProcessedEventRow
app/adapters/outbound/postgres/processed_events.py  PgProcessedEvents
app/adapters/outbound/postgres/migrations/versions/0003_amocrm.py
app/adapters/inbound/http/amocrm.py             build_amocrm_router, parse_note_events, WEBHOOK_PREFIX
app/adapters/inbound/http/security.py           + TunnelGuardMiddleware, client_ip
app/adapters/inbound/http/errors.py             + CRMUnavailable → 503
app/adapters/inbound/http/log_filters.py        MaskWebhookSecretFilter, install_log_filters
app/adapters/inbound/http/app.py                подключение роутера/middleware/key_func
app/adapters/inbound/cli.py                     + amocrm-auth, amocrm-seed, amocrm-say
app/bootstrap.py                                сборка AmoCRM, amocrm_webhook_registration
app/container.py                                + handle_incoming, amocrm
app/main.py                                     регистрация вебхука, фильтр логов
docker-compose.yml, Makefile, .env.example, README.md, CLAUDE.md
tests/fakes.py                                  + RecordingPublisher, InMemoryProcessedEvents, InMemoryTokenStore
tests/unit/test_config.py, test_handle_incoming.py, test_answer_inquiry.py,
tests/unit/test_amocrm_oauth.py, test_amocrm_client.py, test_amocrm_gateway.py,
tests/unit/test_amocrm_webhooks.py, test_log_filters.py, test_bootstrap.py, test_cli.py
tests/api/test_amocrm_webhook.py
tests/integration/test_amocrm_storage.py
```

---

### Task 1: Настройки, доменная ошибка CRM, id сообщений

**Files:**
- Modify: `app/config.py`, `app/domain/errors.py`, `app/domain/models.py`, `pyproject.toml`/`poetry.lock` (cryptography)
- Test: `tests/unit/test_config.py`, `tests/unit/test_domain.py`

**Interfaces:**
- Produces: `Settings.crm_provider: Literal["mock","amocrm"]`, `amocrm_subdomain: str | None`, `amocrm_client_id: str | None`, `amocrm_client_secret: SecretStr | None`, `amocrm_redirect_uri: str`, `amocrm_token_key: SecretStr | None`, `amocrm_webhook_secret: SecretStr | None`, `amocrm_tunnel_metrics_url: str | None`, `amocrm_webhook_rate_limit: str`, `amocrm_timeout_s: float`; `CRMUnavailable(DomainError)`; `DialogMessage(role, text, id: str = "")`.

- [ ] **Step 1: Зависимость**

Run: `poetry add cryptography`

- [ ] **Step 2: Падающие тесты**

Дописать в `tests/unit/test_config.py`:

```python
AMOCRM = {
    "crm_provider": "amocrm",
    "amocrm_subdomain": "demo-shop",
    "amocrm_client_id": "client-id",
    "amocrm_client_secret": "client-secret",
    "amocrm_token_key": "k" * 44,
    "amocrm_webhook_secret": "w" * 32,
}


def test_crm_provider_defaults_to_mock() -> None:
    settings = make(llm_provider="fake")
    assert settings.crm_provider == "mock"
    assert settings.amocrm_redirect_uri == "https://example.com"
    assert settings.amocrm_webhook_rate_limit == "60/minute"


def test_amocrm_provider_accepts_full_settings() -> None:
    settings = make(llm_provider="fake", **AMOCRM)
    assert settings.amocrm_subdomain == "demo-shop"


@pytest.mark.parametrize("missing", ["amocrm_subdomain", "amocrm_client_secret", "amocrm_token_key"])
def test_amocrm_provider_requires_settings(missing: str) -> None:
    with pytest.raises(ValidationError, match=missing.upper()):
        make(llm_provider="fake", **{**AMOCRM, missing: None})


@pytest.mark.parametrize("subdomain", ["evil.com/x", "Demo", "a b", "x" * 64])
def test_amocrm_subdomain_is_restricted(subdomain: str) -> None:
    with pytest.raises(ValidationError):
        make(llm_provider="fake", **{**AMOCRM, "amocrm_subdomain": subdomain})


def test_short_webhook_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make(llm_provider="fake", **{**AMOCRM, "amocrm_webhook_secret": "short"})
```

Дописать в `tests/unit/test_domain.py`:

```python
from app.domain.models import DialogMessage, Role


def test_dialog_message_id_is_optional() -> None:
    assert DialogMessage(Role.CLIENT, "привет").id == ""
    assert DialogMessage(Role.CLIENT, "привет", id="42").id == "42"
```

- [ ] **Step 3: FAIL**

Run: `poetry run pytest tests/unit/test_config.py tests/unit/test_domain.py -q`
Expected: FAIL — нет поля `crm_provider` / `id`.

- [ ] **Step 4: Реализация**

`app/domain/models.py` — `DialogMessage`:

```python
@dataclass(frozen=True, slots=True)
class DialogMessage:
    role: Role
    text: str
    id: str = ""
```

`app/domain/errors.py` — добавить:

```python
class CRMUnavailable(DomainError):
    pass
```

`app/config.py` — поля после `crm_fixture_path` и второй валидатор:

```python
    crm_provider: Literal["mock", "amocrm"] = "mock"
    amocrm_subdomain: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,63}$")
    amocrm_client_id: str | None = None
    amocrm_client_secret: SecretStr | None = None
    amocrm_redirect_uri: str = "https://example.com"
    amocrm_token_key: SecretStr | None = None
    amocrm_webhook_secret: SecretStr | None = Field(default=None, min_length=32)
    amocrm_tunnel_metrics_url: str | None = None
    amocrm_webhook_rate_limit: str = "60/minute"
    amocrm_timeout_s: float = 15.0
```

```python
    @model_validator(mode="after")
    def _require_amocrm_settings(self) -> Self:
        if self.crm_provider != "amocrm":
            return self
        required = (
            "amocrm_subdomain",
            "amocrm_client_id",
            "amocrm_client_secret",
            "amocrm_token_key",
            "amocrm_webhook_secret",
        )
        missing = [name.upper() for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"CRM_PROVIDER=amocrm requires: {', '.join(missing)}")
        return self
```

- [ ] **Step 5: PASS**

Run: `poetry run pytest -q && poetry run ruff check . && poetry run ruff format --check . && poetry run mypy app`
Expected: всё зелёное.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml poetry.lock app/config.py app/domain tests/unit/test_config.py tests/unit/test_domain.py
git commit -m "feat: add AmoCRM settings and CRM domain error"
```

---

### Task 2: Use case обработки входящего сообщения

**Files:**
- Modify: `app/application/ports.py`, `app/application/answer_inquiry.py`, `tests/fakes.py`
- Create: `app/application/handle_incoming.py`
- Test: `tests/unit/test_answer_inquiry.py`, `tests/unit/test_handle_incoming.py`

**Interfaces:**
- Consumes: `AnswerInquiryUseCase`, `CRMGateway`, `DialogMessage.id`, `LeadNotFound`, `LLMUnavailable`, `KnowledgeBaseUnavailable`
- Produces:
  - `AnswerPublisher.publish(lead_id: str, answer: AssistantAnswer) -> None`, `.publish_unavailable(lead_id: str) -> None`
  - `ProcessedEvents.first_seen(key: str) -> bool`
  - `AnswerInquiryUseCase.answer(message: str, dialog: Sequence[DialogMessage]) -> AssistantAnswer`
  - `HandleResult(StrEnum)`: `PROCESSED`, `DUPLICATE`, `IGNORED`, `UNAVAILABLE`
  - `HandleIncomingMessageUseCase(crm, answer_inquiry, publisher, processed_events)`, `async execute(lead_id: str, message_id: str) -> HandleResult`
  - `MAX_MESSAGE_LENGTH = 2000`
  - `tests/fakes.py`: `RecordingPublisher` (`.published: list[tuple[str, AssistantAnswer]]`, `.unavailable: list[str]`), `InMemoryProcessedEvents`

- [ ] **Step 1: Фейки**

Дописать в `tests/fakes.py` (импорт `AssistantAnswer` из `app.domain.models`):

```python
class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, AssistantAnswer]] = []
        self.unavailable: list[str] = []

    async def publish(self, lead_id: str, answer: AssistantAnswer) -> None:
        self.published.append((lead_id, answer))

    async def publish_unavailable(self, lead_id: str) -> None:
        self.unavailable.append(lead_id)


class InMemoryProcessedEvents:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def first_seen(self, key: str) -> bool:
        if key in self.keys:
            return False
        self.keys.add(key)
        return True
```

- [ ] **Step 2: Падающие тесты**

Дописать в `tests/unit/test_answer_inquiry.py`:

```python
async def test_answer_uses_given_dialog_without_crm_lookup() -> None:
    llm = ScriptedLLM([ok([])])
    use_case = await make_use_case(llm)
    dialog = [DialogMessage(Role.CLIENT, "Раньше брал Zeolite Standard")]
    answer = await use_case.answer("Как принимать цеолит?", dialog)
    assert answer.fallback is False
    _, user_prompt = llm.calls[0]
    assert "Раньше брал Zeolite Standard" in user_prompt
```

`tests/unit/test_handle_incoming.py`:

```python
from app.adapters.outbound.crm.mock import MockCRMGateway
from app.adapters.outbound.fake.embedder import FakeEmbedder
from app.application.answer_inquiry import AnswerInquiryUseCase
from app.application.handle_incoming import (
    MAX_MESSAGE_LENGTH,
    HandleIncomingMessageUseCase,
    HandleResult,
)
from app.application.ports import LLMResult
from app.domain.errors import LLMUnavailable
from app.domain.models import DialogMessage, Lead, Role, TokenUsage
from tests.fakes import (
    InMemoryKnowledgeRepository,
    InMemoryProcessedEvents,
    RecordingPublisher,
    ScriptedLLM,
)

LEAD = Lead(
    id="100",
    name="Марина",
    dialog=(
        DialogMessage(Role.CLIENT, "Дороговато для меня", id="1"),
        DialogMessage(Role.MANAGER, "Есть формат поменьше", id="2"),
        DialogMessage(Role.CLIENT, "Как принимать цеолит?", id="3"),
        DialogMessage(Role.CLIENT, "   ", id="4"),
        DialogMessage(Role.CLIENT, "я" * (MAX_MESSAGE_LENGTH + 500), id="5"),
    ),
)
OK = LLMResult(
    arguments={"client_reply": "Здравствуйте!", "manager_hint": "Mini", "used_chunk_ids": []},
    usage=TokenUsage(prompt=10, completion=5),
)


def make(llm: ScriptedLLM) -> tuple[HandleIncomingMessageUseCase, RecordingPublisher]:
    crm = MockCRMGateway([LEAD])
    answer_inquiry = AnswerInquiryUseCase(
        crm, FakeEmbedder(dim=32), InMemoryKnowledgeRepository(), llm, min_score=0.0
    )
    publisher = RecordingPublisher()
    use_case = HandleIncomingMessageUseCase(
        crm=crm,
        answer_inquiry=answer_inquiry,
        publisher=publisher,
        processed_events=InMemoryProcessedEvents(),
    )
    return use_case, publisher


def dialog_block(prompt: str) -> str:
    return prompt.split("<dialog>")[1].split("</dialog>")[0]


async def test_client_message_is_answered_with_history_before_it() -> None:
    llm = ScriptedLLM([OK])
    use_case, publisher = make(llm)

    result = await use_case.execute("100", "3")

    assert result is HandleResult.PROCESSED
    assert [lead_id for lead_id, _ in publisher.published] == ["100"]
    _, prompt = llm.calls[0]
    assert "Дороговато для меня" in dialog_block(prompt)
    assert "Как принимать цеолит?" not in dialog_block(prompt)
    assert prompt.count("Как принимать цеолит?") == 1


async def test_duplicate_delivery_is_processed_once() -> None:
    llm = ScriptedLLM([OK])
    use_case, publisher = make(llm)
    assert await use_case.execute("100", "3") is HandleResult.PROCESSED
    assert await use_case.execute("100", "3") is HandleResult.DUPLICATE
    assert len(llm.calls) == 1
    assert len(publisher.published) == 1


async def test_manager_message_does_not_trigger() -> None:
    llm = ScriptedLLM([])
    use_case, publisher = make(llm)
    assert await use_case.execute("100", "2") is HandleResult.IGNORED
    assert llm.calls == []
    assert publisher.published == []


async def test_unknown_message_is_ignored() -> None:
    use_case, _ = make(ScriptedLLM([]))
    assert await use_case.execute("100", "999") is HandleResult.IGNORED


async def test_deleted_lead_is_ignored() -> None:
    use_case, _ = make(ScriptedLLM([]))
    assert await use_case.execute("404", "3") is HandleResult.IGNORED


async def test_blank_message_is_ignored_without_llm_call() -> None:
    llm = ScriptedLLM([])
    use_case, _ = make(llm)
    assert await use_case.execute("100", "4") is HandleResult.IGNORED
    assert llm.calls == []


async def test_long_message_is_truncated() -> None:
    llm = ScriptedLLM([OK])
    use_case, _ = make(llm)
    await use_case.execute("100", "5")
    _, prompt = llm.calls[0]
    assert "я" * MAX_MESSAGE_LENGTH in prompt
    assert "я" * (MAX_MESSAGE_LENGTH + 1) not in prompt


async def test_llm_outage_publishes_unavailable_note() -> None:
    use_case, publisher = make(ScriptedLLM([LLMUnavailable("down")]))
    assert await use_case.execute("100", "3") is HandleResult.UNAVAILABLE
    assert publisher.unavailable == ["100"]
    assert publisher.published == []
```

- [ ] **Step 3: FAIL**

Run: `poetry run pytest tests/unit/test_answer_inquiry.py tests/unit/test_handle_incoming.py -q`
Expected: FAIL — нет `answer` и модуля `handle_incoming`.

- [ ] **Step 4: Реализация**

`app/application/ports.py` — добавить (импорт `AssistantAnswer`):

```python
class AnswerPublisher(Protocol):
    async def publish(self, lead_id: str, answer: AssistantAnswer) -> None: ...

    async def publish_unavailable(self, lead_id: str) -> None: ...


class ProcessedEvents(Protocol):
    async def first_seen(self, key: str) -> bool: ...
```

`app/application/answer_inquiry.py` — заменить `execute` и сигнатуру `_retrieve` (добавить `from collections.abc import Sequence`):

```python
    async def execute(self, inquiry: Inquiry) -> AssistantAnswer:
        lead = await self._crm.get_lead(inquiry.lead_id)
        return await self.answer(inquiry.message, lead.dialog)

    async def answer(self, message: str, dialog: Sequence[DialogMessage]) -> AssistantAnswer:
        recent = tuple(dialog)[-self._dialog_max_messages :]
        retrieved = await self._retrieve(message, recent)
        user_prompt = build_user_prompt(message, recent, retrieved)

        usage = TokenUsage()
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                result = await self._llm.complete_structured(
                    SYSTEM_PROMPT, user_prompt, FUNCTION_NAME, ANSWER_SCHEMA
                )
                usage += result.usage
                return parse_answer(result.arguments, retrieved, usage)
            except LLMInvalidOutput as exc:
                logger.warning("invalid LLM output (attempt %d): %s", attempt, exc)
        return fallback_answer(usage)
```

`app/application/handle_incoming.py`:

```python
import logging
from enum import StrEnum

from app.application.answer_inquiry import AnswerInquiryUseCase
from app.application.ports import AnswerPublisher, CRMGateway, ProcessedEvents
from app.domain.errors import KnowledgeBaseUnavailable, LeadNotFound, LLMUnavailable
from app.domain.models import Role

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000


class HandleResult(StrEnum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    UNAVAILABLE = "unavailable"


class HandleIncomingMessageUseCase:
    """Сообщение клиента из CRM → ответ и подсказка обратно в ту же сделку."""

    def __init__(
        self,
        crm: CRMGateway,
        answer_inquiry: AnswerInquiryUseCase,
        publisher: AnswerPublisher,
        processed_events: ProcessedEvents,
    ) -> None:
        self._crm = crm
        self._answer_inquiry = answer_inquiry
        self._publisher = publisher
        self._processed_events = processed_events

    async def execute(self, lead_id: str, message_id: str) -> HandleResult:
        # CRM может доставить событие повторно — отвечать клиенту дважды нельзя
        if not await self._processed_events.first_seen(f"crm:message:{message_id}"):
            return HandleResult.DUPLICATE
        try:
            lead = await self._crm.get_lead(lead_id)
        except LeadNotFound:
            logger.warning("lead %s disappeared before processing", lead_id)
            return HandleResult.IGNORED

        position = next((i for i, m in enumerate(lead.dialog) if m.id == message_id), None)
        if position is None or lead.dialog[position].role is not Role.CLIENT:
            return HandleResult.IGNORED
        text = lead.dialog[position].text.strip()[:MAX_MESSAGE_LENGTH]
        if not text:
            return HandleResult.IGNORED

        try:
            answer = await self._answer_inquiry.answer(text, lead.dialog[:position])
        except (LLMUnavailable, KnowledgeBaseUnavailable) as exc:
            logger.error("assistant unavailable for lead %s: %s", lead_id, exc)
            await self._publisher.publish_unavailable(lead_id)
            return HandleResult.UNAVAILABLE
        await self._publisher.publish(lead_id, answer)
        return HandleResult.PROCESSED
```

- [ ] **Step 5: PASS**

Run: `poetry run pytest -q && poetry run ruff check . && poetry run ruff format --check . && poetry run mypy app`
Expected: всё зелёное (старые тесты `execute` не меняются).

- [ ] **Step 6: Commit**

```bash
git add app/application tests/fakes.py tests/unit/test_answer_inquiry.py tests/unit/test_handle_incoming.py
git commit -m "feat: add use case for incoming CRM messages"
```

---

### Task 3: Хранилище токенов и обработанных событий (Postgres)

**Files:**
- Modify: `app/adapters/outbound/postgres/models.py`
- Create: `app/adapters/outbound/amocrm/__init__.py`, `app/adapters/outbound/amocrm/errors.py`, `app/adapters/outbound/amocrm/tokens.py`, `app/adapters/outbound/amocrm/token_store.py`, `app/adapters/outbound/postgres/processed_events.py`, `app/adapters/outbound/postgres/migrations/versions/0003_amocrm.py`, `tests/integration/test_amocrm_storage.py`
- Modify: `tests/fakes.py` (`InMemoryTokenStore`)

**Interfaces:**
- Produces:
  - `AmoCRMAuthError(CRMUnavailable)`, `AmoCRMNotFound(CRMUnavailable)`
  - `TokenPair(access_token: str, refresh_token: str, expires_at: float)`; `TokenStore` Protocol: `load() -> TokenPair | None`, `save(pair: TokenPair) -> None`
  - `PgTokenStore(sessionmaker, encryption_key: SecretStr)`
  - `PgProcessedEvents(sessionmaker)` реализует `ProcessedEvents`
  - `tests/fakes.py`: `InMemoryTokenStore(pair: TokenPair | None = None)` с `.saved: list[TokenPair]`

- [ ] **Step 1: Падающий интеграционный тест**

`tests/integration/test_amocrm_storage.py`:

```python
import os
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.outbound.amocrm.errors import AmoCRMAuthError
from app.adapters.outbound.amocrm.token_store import PgTokenStore
from app.adapters.outbound.amocrm.tokens import TokenPair
from app.adapters.outbound.postgres.processed_events import PgProcessedEvents
from tests.integration.test_pgvector_repository import alembic

pytestmark = pytest.mark.integration

PAIR = TokenPair(access_token="access-secret", refresh_token="refresh-secret", expires_at=2e9)


@pytest.fixture
async def sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await alembic(monkeypatch, "downgrade", "base")
    await alembic(monkeypatch, "upgrade", "head")
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


async def test_tokens_roundtrip_and_are_encrypted_at_rest(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    store = PgTokenStore(sessionmaker, key())
    assert await store.load() is None
    await store.save(PAIR)
    await store.save(TokenPair("access-2", "refresh-2", 2e9 + 10))

    assert await store.load() == TokenPair("access-2", "refresh-2", 2e9 + 10)
    async with sessionmaker() as session:
        rows = (await session.execute(text("SELECT access_token, refresh_token FROM amocrm_tokens"))).all()
    assert len(rows) == 1
    assert b"access-2" not in bytes(rows[0][0])
    assert b"refresh-2" not in bytes(rows[0][1])


async def test_tokens_with_another_key_are_rejected(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await PgTokenStore(sessionmaker, key()).save(PAIR)
    with pytest.raises(AmoCRMAuthError, match="amocrm-auth"):
        await PgTokenStore(sessionmaker, key()).load()


async def test_processed_events_are_seen_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    events = PgProcessedEvents(sessionmaker)
    assert await events.first_seen("crm:message:1") is True
    assert await events.first_seen("crm:message:1") is False
    assert await events.first_seen("crm:message:2") is True
```

- [ ] **Step 2: FAIL в Docker**

Run: `make test-integration`
Expected: FAIL — `ModuleNotFoundError: app.adapters.outbound.amocrm`.

- [ ] **Step 3: Реализация**

`app/adapters/outbound/amocrm/__init__.py` — пустой.

`app/adapters/outbound/amocrm/errors.py`:

```python
from app.domain.errors import CRMUnavailable


class AmoCRMAuthError(CRMUnavailable):
    pass


class AmoCRMNotFound(CRMUnavailable):
    pass
```

`app/adapters/outbound/amocrm/tokens.py`:

```python
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
```

`app/adapters/outbound/postgres/models.py` — добавить (импорты `datetime`, `DateTime`, `LargeBinary`, `SmallInteger`, `func`):

```python
class AmoCRMTokenRow(Base):
    __tablename__ = "amocrm_tokens"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    access_token: Mapped[bytes] = mapped_column(LargeBinary)
    refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProcessedEventRow(Base):
    __tablename__ = "processed_events"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

`app/adapters/outbound/amocrm/token_store.py`:

```python
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.outbound.amocrm.errors import AmoCRMAuthError
from app.adapters.outbound.amocrm.tokens import TokenPair
from app.adapters.outbound.postgres.models import AmoCRMTokenRow

# Интеграция одна на аккаунт — храним единственную строку
_ROW_ID = 1


class PgTokenStore:
    """Токены AmoCRM в Postgres, зашифрованные Fernet: дамп БД не раскрывает доступ к CRM."""

    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], encryption_key: SecretStr
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fernet = Fernet(encryption_key.get_secret_value().encode())

    async def load(self) -> TokenPair | None:
        async with self._sessionmaker() as session:
            row = await session.get(AmoCRMTokenRow, _ROW_ID)
        if row is None:
            return None
        try:
            return TokenPair(
                access_token=self._fernet.decrypt(row.access_token).decode(),
                refresh_token=self._fernet.decrypt(row.refresh_token).decode(),
                expires_at=row.expires_at.timestamp(),
            )
        except InvalidToken as exc:
            raise AmoCRMAuthError(
                "stored AmoCRM tokens cannot be decrypted (AMOCRM_TOKEN_KEY changed?); "
                "run make amocrm-auth code=<code>"
            ) from exc

    async def save(self, pair: TokenPair) -> None:
        values = {
            "access_token": self._fernet.encrypt(pair.access_token.encode()),
            "refresh_token": self._fernet.encrypt(pair.refresh_token.encode()),
            "expires_at": datetime.fromtimestamp(pair.expires_at, UTC),
        }
        stmt = (
            insert(AmoCRMTokenRow)
            .values(id=_ROW_ID, **values)
            .on_conflict_do_update(
                index_elements=[AmoCRMTokenRow.id], set_={**values, "updated_at": func.now()}
            )
        )
        async with self._sessionmaker() as session, session.begin():
            await session.execute(stmt)
```

`app/adapters/outbound/postgres/processed_events.py`:

```python
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.outbound.postgres.models import ProcessedEventRow


class PgProcessedEvents:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def first_seen(self, key: str) -> bool:
        # Атомарно через уникальный ключ: два параллельных вебхука не пройдут оба
        stmt = (
            insert(ProcessedEventRow)
            .values(key=key)
            .on_conflict_do_nothing()
            .returning(ProcessedEventRow.key)
        )
        async with self._sessionmaker() as session, session.begin():
            inserted = await session.scalar(stmt)
        return inserted is not None
```

`app/adapters/outbound/postgres/migrations/versions/0003_amocrm.py`:

```python
"""AmoCRM OAuth tokens and processed webhook events

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amocrm_tokens",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("access_token", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "processed_events",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("processed_events")
    op.drop_table("amocrm_tokens")
```

`tests/fakes.py` — добавить (импорт `TokenPair`):

```python
class InMemoryTokenStore:
    def __init__(self, pair: TokenPair | None = None) -> None:
        self.pair = pair
        self.saved: list[TokenPair] = []

    async def load(self) -> TokenPair | None:
        return self.pair

    async def save(self, pair: TokenPair) -> None:
        self.pair = pair
        self.saved.append(pair)
```

- [ ] **Step 4: PASS**

Run: `make test-integration && poetry run pytest -q && poetry run ruff check . && poetry run mypy app`
Expected: интеграционные 13 passed (10 старых + 3 новых), unit зелёные.

- [ ] **Step 5: Commit**

```bash
git add app/adapters/outbound/amocrm app/adapters/outbound/postgres tests/fakes.py tests/integration/test_amocrm_storage.py
git commit -m "feat: store encrypted AmoCRM tokens and processed events"
```

---

### Task 4: OAuth AmoCRM с ротацией токенов

**Files:**
- Create: `app/adapters/outbound/amocrm/oauth.py`, `tests/unit/test_amocrm_oauth.py`

**Interfaces:**
- Consumes: `TokenPair`, `TokenStore`, `AmoCRMAuthError`, `CRMUnavailable`
- Produces: `AmoCRMOAuth(http, *, subdomain: str, client_id: str, client_secret: SecretStr, redirect_uri: str, store: TokenStore, clock=time.time, refresh_margin_s=60.0)`; `async exchange_code(code: str) -> None`; `async access_token() -> str`; `invalidate() -> None`

- [ ] **Step 1: Падающие тесты**

`tests/unit/test_amocrm_oauth.py`:

```python
import asyncio
import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.adapters.outbound.amocrm.errors import AmoCRMAuthError
from app.adapters.outbound.amocrm.oauth import AmoCRMOAuth
from app.adapters.outbound.amocrm.tokens import TokenPair
from app.domain.errors import CRMUnavailable
from tests.fakes import InMemoryTokenStore

TOKEN_URL = "https://demo.amocrm.ru/oauth2/access_token"
NOW = 1_000_000.0


def tokens(access: str, refresh: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "token_type": "Bearer",
            "expires_in": 86400,
            "access_token": access,
            "refresh_token": refresh,
        },
    )


def make(http: httpx.AsyncClient, store: InMemoryTokenStore, now: list[float]) -> AmoCRMOAuth:
    return AmoCRMOAuth(
        http,
        subdomain="demo",
        client_id="cid",
        client_secret=SecretStr("csecret"),
        redirect_uri="https://example.com",
        store=store,
        clock=lambda: now[0],
    )


async def test_exchange_code_saves_token_pair(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(TOKEN_URL).mock(return_value=tokens("a1", "r1"))
    store = InMemoryTokenStore()
    async with httpx.AsyncClient() as http:
        await make(http, store, [NOW]).exchange_code("auth-code")
    assert store.saved == [TokenPair("a1", "r1", NOW + 86400)]
    assert json.loads(route.calls[0].request.content) == {
        "client_id": "cid",
        "client_secret": "csecret",
        "redirect_uri": "https://example.com",
        "grant_type": "authorization_code",
        "code": "auth-code",
    }


async def test_fresh_token_is_served_without_http(respx_mock: respx.MockRouter) -> None:
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW + 3600))
    async with httpx.AsyncClient() as http:
        assert await make(http, store, [NOW]).access_token() == "a1"
    assert respx_mock.calls.call_count == 0


async def test_expired_token_is_refreshed_and_rotation_saved(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(TOKEN_URL).mock(return_value=tokens("a2", "r2"))
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW + 30))
    async with httpx.AsyncClient() as http:
        assert await make(http, store, [NOW]).access_token() == "a2"
    body = json.loads(route.calls[0].request.content)
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "r1"
    assert store.saved[-1] == TokenPair("a2", "r2", NOW + 86400)


async def test_concurrent_callers_refresh_once(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(TOKEN_URL).mock(return_value=tokens("a2", "r2"))
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW - 1))
    async with httpx.AsyncClient() as http:
        oauth = make(http, store, [NOW])
        results = await asyncio.gather(*(oauth.access_token() for _ in range(10)))
    assert set(results) == {"a2"}
    assert route.call_count == 1


async def test_invalidate_forces_refresh(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=tokens("a2", "r2"))
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW + 3600))
    async with httpx.AsyncClient() as http:
        oauth = make(http, store, [NOW])
        assert await oauth.access_token() == "a1"
        oauth.invalidate()
        assert await oauth.access_token() == "a2"


async def test_not_authorized_yet() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(AmoCRMAuthError, match="amocrm-auth"):
            await make(http, InMemoryTokenStore(), [NOW]).access_token()


async def test_rejected_refresh_token_asks_for_new_code(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"hint": "Token has been revoked"})
    )
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW - 1))
    async with httpx.AsyncClient() as http:
        with pytest.raises(AmoCRMAuthError, match="amocrm-auth"):
            await make(http, store, [NOW]).access_token()


async def test_network_error_is_crm_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
    store = InMemoryTokenStore(TokenPair("a1", "r1", NOW - 1))
    async with httpx.AsyncClient() as http:
        with pytest.raises(CRMUnavailable):
            await make(http, store, [NOW]).access_token()
```

- [ ] **Step 2: FAIL**

Run: `poetry run pytest tests/unit/test_amocrm_oauth.py -q`
Expected: FAIL — нет модуля `oauth`.

- [ ] **Step 3: Реализация `app/adapters/outbound/amocrm/oauth.py`**

```python
import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
from pydantic import SecretStr

from app.adapters.outbound.amocrm.errors import AmoCRMAuthError
from app.adapters.outbound.amocrm.tokens import TokenPair, TokenStore
from app.domain.errors import CRMUnavailable

logger = logging.getLogger(__name__)

_REAUTH_HINT = "run make amocrm-auth code=<authorization code from the integration card>"


class AmoCRMOAuth:
    """OAuth 2.0 AmoCRM: access-токен живёт сутки, refresh-токен одноразовый."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        subdomain: str,
        client_id: str,
        client_secret: SecretStr,
        redirect_uri: str,
        store: TokenStore,
        clock: Callable[[], float] = time.time,
        refresh_margin_s: float = 60.0,
    ) -> None:
        self._http = http
        self._token_url = f"https://{subdomain}.amocrm.ru/oauth2/access_token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._store = store
        self._clock = clock
        self._refresh_margin_s = refresh_margin_s
        self._cached: TokenPair | None = None
        self._lock = asyncio.Lock()

    async def exchange_code(self, code: str) -> None:
        pair = await self._request({"grant_type": "authorization_code", "code": code})
        await self._store.save(pair)
        self._cached = pair

    async def access_token(self) -> str:
        if self._cached is not None and self._is_fresh(self._cached):
            return self._cached.access_token
        async with self._lock:
            pair = self._cached or await self._store.load()
            if pair is None:
                raise AmoCRMAuthError(f"AmoCRM is not authorized; {_REAUTH_HINT}")
            # Пока ждали lock, токен мог обновить другой запрос
            if not self._is_fresh(pair):
                pair = await self._refresh(pair)
            self._cached = pair
            return pair.access_token

    def invalidate(self) -> None:
        if self._cached is not None:
            self._cached = replace(self._cached, expires_at=0.0)

    def _is_fresh(self, pair: TokenPair) -> bool:
        return self._clock() < pair.expires_at - self._refresh_margin_s

    async def _refresh(self, pair: TokenPair) -> TokenPair:
        new_pair = await self._request(
            {"grant_type": "refresh_token", "refresh_token": pair.refresh_token}
        )
        # Старый refresh-токен уже сгорел: новую пару сохраняем до того, как ей пользоваться
        await self._store.save(new_pair)
        logger.info("AmoCRM access token refreshed")
        return new_pair

    async def _request(self, grant: dict[str, str]) -> TokenPair:
        payload: dict[str, Any] = {
            "client_id": self._client_id,
            "client_secret": self._client_secret.get_secret_value(),
            "redirect_uri": self._redirect_uri,
            **grant,
        }
        try:
            response = await self._http.post(self._token_url, json=payload)
        except httpx.HTTPError as exc:
            raise CRMUnavailable("AmoCRM OAuth request failed") from exc
        if response.status_code in (400, 401):
            raise AmoCRMAuthError(f"AmoCRM rejected the OAuth grant; {_REAUTH_HINT}")
        if response.status_code != httpx.codes.OK:
            raise CRMUnavailable(f"AmoCRM OAuth failed with status {response.status_code}")
        data = response.json()
        return TokenPair(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=self._clock() + int(data["expires_in"]),
        )
```

- [ ] **Step 4: PASS**

Run: `poetry run pytest -q && poetry run ruff check . && poetry run mypy app`

- [ ] **Step 5: Commit**

```bash
git add app/adapters/outbound/amocrm/oauth.py tests/unit/test_amocrm_oauth.py
git commit -m "feat: add AmoCRM OAuth with refresh token rotation"
```

---

### Task 5: HTTP-клиент и gateway AmoCRM

**Files:**
- Create: `app/adapters/outbound/amocrm/client.py`, `app/adapters/outbound/amocrm/gateway.py`, `tests/unit/test_amocrm_client.py`, `tests/unit/test_amocrm_gateway.py`

**Interfaces:**
- Consumes: `AmoCRMOAuth` (через Protocol `AccessTokenSource`: `access_token()`, `invalidate()`), `AmoCRMNotFound`, `CRMUnavailable`, `LeadNotFound`, `Lead`, `DialogMessage`, `Role`, `AssistantAnswer`
- Produces:
  - `AmoCRMClient(http, subdomain, tokens, *, max_retries=2, backoff_s=0.5, sleep=asyncio.sleep)`: `get(path, params=None) -> dict | None`, `post(path, payload) -> dict | None`, `delete(path, payload) -> None`
  - `AmoCRMGateway(client)`: `get_lead`, `list_leads`, `publish`, `publish_unavailable`, `create_lead(name: str) -> str`, `add_message(lead_id: str, role: Role, text: str) -> str`
  - `format_answer_note(answer: AssistantAnswer) -> str`, `SERVICE_NAME`, `UNAVAILABLE_TEXT`, `DEMO_TAG = "o-complex-demo"`, `LIST_LIMIT = 5`

- [ ] **Step 1: Падающие тесты клиента**

`tests/unit/test_amocrm_client.py`:

```python
import json

import httpx
import pytest
import respx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.errors import AmoCRMNotFound
from app.domain.errors import CRMUnavailable

API = "https://demo.amocrm.ru/api/v4"


class StubTokens:
    def __init__(self) -> None:
        self.token = "t1"
        self.invalidated = 0

    async def access_token(self) -> str:
        return self.token

    def invalidate(self) -> None:
        self.invalidated += 1
        self.token = "t2"


async def no_sleep(_: float) -> None:
    return None


def make(http: httpx.AsyncClient, tokens: StubTokens | None = None) -> AmoCRMClient:
    return AmoCRMClient(http, "demo", tokens or StubTokens(), backoff_s=0, sleep=no_sleep)


async def test_get_sends_bearer_and_params(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{API}/leads/1").mock(return_value=httpx.Response(200, json={"id": 1}))
    async with httpx.AsyncClient() as http:
        assert await make(http).get("/leads/1", params=[("with", "contacts")]) == {"id": 1}
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer t1"
    assert request.url.params["with"] == "contacts"


async def test_no_content_is_none(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        assert await make(http).get("/leads") is None


async def test_unauthorized_refreshes_token_once(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/leads/notes").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"ok": True})]
    )
    tokens = StubTokens()
    async with httpx.AsyncClient() as http:
        await make(http, tokens).post("/leads/notes", [{"a": 1}])
    assert tokens.invalidated == 1
    assert route.calls[1].request.headers["Authorization"] == "Bearer t2"
    assert json.loads(route.calls[1].request.content) == [{"a": 1}]


async def test_rate_limit_and_server_errors_are_retried(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{API}/leads").mock(
        side_effect=[httpx.Response(429), httpx.Response(502), httpx.Response(200, json={})]
    )
    async with httpx.AsyncClient() as http:
        await make(http).get("/leads")
    assert route.call_count == 3


async def test_gives_up_with_crm_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(CRMUnavailable):
            await make(http).get("/leads")


async def test_not_found_is_distinct_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads/9").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        with pytest.raises(AmoCRMNotFound):
            await make(http).get("/leads/9")


async def test_delete_sends_json_body(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.delete(f"{API}/webhooks").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        await make(http).delete("/webhooks", {"destination": "https://x"})
    assert json.loads(route.calls[0].request.content) == {"destination": "https://x"}
```

- [ ] **Step 2: Падающие тесты gateway**

`tests/unit/test_amocrm_gateway.py`:

```python
import json

import httpx
import pytest
import respx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.gateway import (
    DEMO_TAG,
    SERVICE_NAME,
    UNAVAILABLE_TEXT,
    AmoCRMGateway,
    format_answer_note,
)
from app.domain.errors import LeadNotFound
from app.domain.models import AssistantAnswer, Role, Source, TokenUsage
from tests.unit.test_amocrm_client import API, StubTokens, no_sleep

ANSWER = AssistantAnswer(
    client_reply="Здравствуйте! Принимайте курсом.",
    manager_hint="Предложите Zeolite Mini.",
    sources=(Source("zeolite-mini", "Zeolite Mini"), Source("objections", "Возражения")),
    usage=TokenUsage(prompt=1, completion=1),
)


def note(note_id: int, note_type: str, text: str) -> dict[str, object]:
    return {"id": note_id, "entity_id": 7, "note_type": note_type, "params": {"text": text}}


def notes_page(notes: list[dict[str, object]], has_next: bool = False) -> httpx.Response:
    links: dict[str, object] = {"self": {"href": "x"}}
    if has_next:
        links["next"] = {"href": "y"}
    return httpx.Response(200, json={"_links": links, "_embedded": {"notes": notes}})


def gateway(http: httpx.AsyncClient) -> AmoCRMGateway:
    return AmoCRMGateway(AmoCRMClient(http, "demo", StubTokens(), backoff_s=0, sleep=no_sleep))


async def test_lead_dialog_maps_sms_notes_and_skips_others(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "name": "Марина"})
    )
    notes_route = respx_mock.get(f"{API}/leads/7/notes").mock(
        side_effect=[
            notes_page(
                [note(1, "sms_in", "Дорого"), note(2, "service_message", "наш ответ")],
                has_next=True,
            ),
            notes_page([note(3, "sms_out", "Есть Mini"), note(4, "common", "внутреннее")]),
        ]
    )
    async with httpx.AsyncClient() as http:
        lead = await gateway(http).get_lead("7")

    assert lead.id == "7"
    assert lead.name == "Марина"
    assert [(m.id, m.role, m.text) for m in lead.dialog] == [
        ("1", Role.CLIENT, "Дорого"),
        ("3", Role.MANAGER, "Есть Mini"),
    ]
    first = notes_route.calls[0].request.url.params
    assert first.get_list("filter[note_type][]") == ["sms_in", "sms_out"]
    assert first["order[id]"] == "asc"
    assert notes_route.calls[1].request.url.params["page"] == "2"


async def test_lead_without_notes(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads/7").mock(return_value=httpx.Response(200, json={"id": 7}))
    respx_mock.get(f"{API}/leads/7/notes").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        lead = await gateway(http).get_lead("7")
    assert lead.dialog == ()
    assert lead.name == "Сделка 7"


@pytest.mark.parametrize("lead_id", ["abc", "../1", ""])
async def test_non_numeric_lead_id_is_not_found(lead_id: str) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(LeadNotFound):
            await gateway(http).get_lead(lead_id)


async def test_missing_lead_is_not_found(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/leads/7").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        with pytest.raises(LeadNotFound):
            await gateway(http).get_lead("7")


async def test_list_leads_takes_recent_ones(respx_mock: respx.MockRouter) -> None:
    list_route = respx_mock.get(f"{API}/leads").mock(
        return_value=httpx.Response(200, json={"_embedded": {"leads": [{"id": 7}]}})
    )
    respx_mock.get(f"{API}/leads/7").mock(return_value=httpx.Response(200, json={"id": 7, "name": "A"}))
    respx_mock.get(f"{API}/leads/7/notes").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        leads = await gateway(http).list_leads()
    assert [lead.id for lead in leads] == ["7"]
    params = list_route.calls[0].request.url.params
    assert params["order[updated_at]"] == "desc"
    assert params["limit"] == "5"


async def test_publish_writes_service_message(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/leads/notes").mock(
        return_value=httpx.Response(200, json={"_embedded": {"notes": [{"id": 9}]}})
    )
    async with httpx.AsyncClient() as http:
        await gateway(http).publish("7", ANSWER)
    [body] = json.loads(route.calls[0].request.content)
    assert body["entity_id"] == 7
    assert body["note_type"] == "service_message"
    assert body["params"] == {"service": SERVICE_NAME, "text": format_answer_note(ANSWER)}


async def test_fallback_answer_is_published_as_unavailable(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/leads/notes").mock(
        return_value=httpx.Response(200, json={"_embedded": {"notes": [{"id": 9}]}})
    )
    fallback = AssistantAnswer("x", "y", (), TokenUsage(), fallback=True)
    async with httpx.AsyncClient() as http:
        await gateway(http).publish("7", fallback)
        await gateway(http).publish_unavailable("7")
    texts = [json.loads(c.request.content)[0]["params"]["text"] for c in route.calls]
    assert texts == [UNAVAILABLE_TEXT, UNAVAILABLE_TEXT]


def test_note_text_has_reply_sources_and_hint() -> None:
    text = format_answer_note(ANSWER)
    assert text.index("Здравствуйте!") < text.index("Zeolite Mini, Возражения")
    assert text.index("Zeolite Mini, Возражения") < text.index("Предложите Zeolite Mini.")


async def test_demo_helpers_create_lead_and_messages(respx_mock: respx.MockRouter) -> None:
    leads_route = respx_mock.post(f"{API}/leads").mock(
        return_value=httpx.Response(200, json={"_embedded": {"leads": [{"id": 55}]}})
    )
    notes_route = respx_mock.post(f"{API}/leads/notes").mock(
        return_value=httpx.Response(200, json={"_embedded": {"notes": [{"id": 77}]}})
    )
    async with httpx.AsyncClient() as http:
        gw = gateway(http)
        assert await gw.create_lead("Анна") == "55"
        assert await gw.add_message("55", Role.CLIENT, "Привет") == "77"
        await gw.add_message("55", Role.MANAGER, "Здравствуйте")
    [lead] = json.loads(leads_route.calls[0].request.content)
    assert lead["_embedded"]["tags"] == [{"name": DEMO_TAG}]
    types = [json.loads(c.request.content)[0]["note_type"] for c in notes_route.calls]
    assert types == ["sms_in", "sms_out"]
```

- [ ] **Step 3: FAIL**

Run: `poetry run pytest tests/unit/test_amocrm_client.py tests/unit/test_amocrm_gateway.py -q`
Expected: FAIL — нет модулей.

- [ ] **Step 4: Реализация**

`app/adapters/outbound/amocrm/client.py`:

```python
import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import httpx

from app.adapters.outbound.amocrm.errors import AmoCRMNotFound
from app.domain.errors import CRMUnavailable

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

Params = Sequence[tuple[str, str]]


class AccessTokenSource(Protocol):
    async def access_token(self) -> str: ...

    def invalidate(self) -> None: ...


class AmoCRMClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        subdomain: str,
        tokens: AccessTokenSource,
        *,
        max_retries: int = 2,
        backoff_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._base_url = f"https://{subdomain}.amocrm.ru/api/v4"
        self._tokens = tokens
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    async def get(self, path: str, params: Params | None = None) -> dict[str, Any] | None:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, payload: Any) -> dict[str, Any] | None:
        return await self._request("POST", path, json=payload)

    async def delete(self, path: str, payload: Any) -> None:
        await self._request("DELETE", path, json=payload)

    async def _request(
        self, method: str, path: str, *, params: Params | None = None, json: Any = None
    ) -> dict[str, Any] | None:
        url = f"{self._base_url}/{path.lstrip('/')}"
        attempt = 0
        token_refreshed = False
        while True:
            token = await self._tokens.access_token()
            status: int | None = None
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=list(params) if params else None,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
                status = response.status_code
                if status == httpx.codes.NO_CONTENT:
                    return None
                if httpx.codes.is_success(status):
                    data = response.json()
                    return data if isinstance(data, dict) else {"items": data}
                if status == httpx.codes.NOT_FOUND:
                    raise AmoCRMNotFound(f"{method} {path} not found")
            except httpx.HTTPError as exc:
                logger.warning("AmoCRM network error on %s: %s", path, type(exc).__name__)

            if status == httpx.codes.UNAUTHORIZED and not token_refreshed:
                self._tokens.invalidate()
                token_refreshed = True
                continue
            if (status is None or status in _RETRYABLE_STATUSES) and attempt < self._max_retries:
                await self._sleep(self._backoff_s * 2**attempt)
                attempt += 1
                continue
            raise CRMUnavailable(f"AmoCRM {method} {path} failed, status={status}")
```

`app/adapters/outbound/amocrm/gateway.py`:

```python
from typing import Any

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.errors import AmoCRMNotFound
from app.domain.errors import LeadNotFound
from app.domain.models import AssistantAnswer, DialogMessage, Lead, Role

SERVICE_NAME = "Ассистент O-complex"
UNAVAILABLE_TEXT = "Ассистент временно недоступен — ответьте клиенту вручную."
DEMO_TAG = "o-complex-demo"
LIST_LIMIT = 5
# AmoCRM требует телефон у SMS-примечаний; для демо-сообщений подставляем заглушку
DEMO_PHONE = "+70000000000"

_ROLES = {"sms_in": Role.CLIENT, "sms_out": Role.MANAGER}
_NOTE_TYPES = {Role.CLIENT: "sms_in", Role.MANAGER: "sms_out"}
_PAGE_SIZE = "250"


def format_answer_note(answer: AssistantAnswer) -> str:
    lines = ["💬 Ответ клиенту:", answer.client_reply]
    if answer.sources:
        lines += ["", "📚 Источники: " + ", ".join(s.title for s in answer.sources)]
    lines += ["", "💡 Подсказка менеджеру:", answer.manager_hint]
    return "\n".join(lines)


class AmoCRMGateway:
    """Сделки AmoCRM как лиды: SMS-примечания — диалог, service_message — ответ ассистента."""

    def __init__(self, client: AmoCRMClient) -> None:
        self._client = client

    async def get_lead(self, lead_id: str) -> Lead:
        number = _lead_number(lead_id)
        try:
            data = await self._client.get(f"/leads/{number}")
        except AmoCRMNotFound:
            raise LeadNotFound(lead_id) from None
        if data is None:
            raise LeadNotFound(lead_id)
        name = data.get("name") or f"Сделка {number}"
        return Lead(id=str(number), name=str(name), dialog=await self._dialog(number))

    async def list_leads(self) -> list[Lead]:
        data = await self._client.get(
            "/leads", params=[("order[updated_at]", "desc"), ("limit", str(LIST_LIMIT))]
        )
        leads = _embedded(data, "leads")
        return [await self.get_lead(str(item["id"])) for item in leads]

    async def publish(self, lead_id: str, answer: AssistantAnswer) -> None:
        text = UNAVAILABLE_TEXT if answer.fallback else format_answer_note(answer)
        await self._add_note(lead_id, "service_message", {"service": SERVICE_NAME, "text": text})

    async def publish_unavailable(self, lead_id: str) -> None:
        await self._add_note(
            lead_id, "service_message", {"service": SERVICE_NAME, "text": UNAVAILABLE_TEXT}
        )

    async def create_lead(self, name: str) -> str:
        data = await self._client.post(
            "/leads", [{"name": name, "_embedded": {"tags": [{"name": DEMO_TAG}]}}]
        )
        return str(_embedded(data, "leads")[0]["id"])

    async def add_message(self, lead_id: str, role: Role, text: str) -> str:
        data = await self._add_note(
            lead_id, _NOTE_TYPES[role], {"text": text, "phone": DEMO_PHONE}
        )
        return str(_embedded(data, "notes")[0]["id"])

    async def _dialog(self, number: int) -> tuple[DialogMessage, ...]:
        messages: list[DialogMessage] = []
        page = 1
        while True:
            data = await self._client.get(
                f"/leads/{number}/notes",
                params=[
                    ("filter[note_type][]", "sms_in"),
                    ("filter[note_type][]", "sms_out"),
                    ("order[id]", "asc"),
                    ("limit", _PAGE_SIZE),
                    ("page", str(page)),
                ],
            )
            for note in _embedded(data, "notes"):
                role = _ROLES.get(str(note.get("note_type")))
                text = (note.get("params") or {}).get("text")
                # Фильтр на стороне API не гарантирован — проверяем тип ещё раз
                if role is not None and isinstance(text, str):
                    messages.append(DialogMessage(role, text, id=str(note["id"])))
            if not (data or {}).get("_links", {}).get("next"):
                return tuple(messages)
            page += 1

    async def _add_note(
        self, lead_id: str, note_type: str, params: dict[str, str]
    ) -> dict[str, Any] | None:
        return await self._client.post(
            "/leads/notes",
            [{"entity_id": _lead_number(lead_id), "note_type": note_type, "params": params}],
        )


def _lead_number(lead_id: str) -> int:
    if not lead_id.isdigit():
        raise LeadNotFound(lead_id)
    return int(lead_id)


def _embedded(data: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    items = (data or {}).get("_embedded", {}).get(key, [])
    return [item for item in items if isinstance(item, dict)]
```

- [ ] **Step 5: PASS**

Run: `poetry run pytest -q && poetry run ruff check . && poetry run ruff format --check . && poetry run mypy app`

- [ ] **Step 6: Commit**

```bash
git add app/adapters/outbound/amocrm tests/unit/test_amocrm_client.py tests/unit/test_amocrm_gateway.py
git commit -m "feat: add AmoCRM API client and CRM gateway"
```

---

### Task 6: Регистрация вебхука и адрес туннеля

**Files:**
- Create: `app/adapters/outbound/amocrm/webhooks.py`, `tests/unit/test_amocrm_webhooks.py`

**Interfaces:**
- Consumes: `AmoCRMClient`, `CRMUnavailable`
- Produces: `WEBHOOK_EVENTS = ["note_lead"]`, `AmoCRMWebhookRegistrar(client)` с `register(destination)`/`unregister(destination)`; `resolve_tunnel_url(http, metrics_url, *, attempts=15, delay_s=2.0, sleep=asyncio.sleep) -> str`; `webhook_destination(public_url: str, secret: str) -> str`; `WEBHOOK_PATH_PREFIX = "/integrations/amocrm/webhook/"`

- [ ] **Step 1: Падающие тесты**

`tests/unit/test_amocrm_webhooks.py`:

```python
import json

import httpx
import pytest
import respx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.adapters.outbound.amocrm.webhooks import (
    AmoCRMWebhookRegistrar,
    resolve_tunnel_url,
    webhook_destination,
)
from app.domain.errors import CRMUnavailable
from tests.unit.test_amocrm_client import API, StubTokens, no_sleep

METRICS = "http://tunnel:2000/quicktunnel"


def registrar(http: httpx.AsyncClient) -> AmoCRMWebhookRegistrar:
    client = AmoCRMClient(http, "demo", StubTokens(), backoff_s=0, sleep=no_sleep)
    return AmoCRMWebhookRegistrar(client)


def test_destination_contains_secret_path() -> None:
    assert (
        webhook_destination("https://abc.trycloudflare.com", "s" * 32)
        == "https://abc.trycloudflare.com/integrations/amocrm/webhook/" + "s" * 32
    )


async def test_register_subscribes_to_lead_notes(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/webhooks").mock(return_value=httpx.Response(201, json={}))
    async with httpx.AsyncClient() as http:
        await registrar(http).register("https://x/hook")
    assert json.loads(route.calls[0].request.content) == {
        "destination": "https://x/hook",
        "settings": ["note_lead"],
        "sort": 10,
    }


async def test_unregister(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.delete(f"{API}/webhooks").mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        await registrar(http).unregister("https://x/hook")
    assert json.loads(route.calls[0].request.content) == {"destination": "https://x/hook"}


async def test_tunnel_url_waits_until_hostname_appears(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(METRICS).mock(
        side_effect=[
            httpx.ConnectError("not yet"),
            httpx.Response(200, json={"hostname": ""}),
            httpx.Response(200, json={"hostname": "abc.trycloudflare.com"}),
        ]
    )
    async with httpx.AsyncClient() as http:
        url = await resolve_tunnel_url(http, METRICS, delay_s=0, sleep=no_sleep)
    assert url == "https://abc.trycloudflare.com"


async def test_tunnel_never_ready(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(METRICS).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(CRMUnavailable):
            await resolve_tunnel_url(http, METRICS, attempts=3, delay_s=0, sleep=no_sleep)
```

- [ ] **Step 2: FAIL**

Run: `poetry run pytest tests/unit/test_amocrm_webhooks.py -q`
Expected: FAIL — нет модуля.

- [ ] **Step 3: Реализация `app/adapters/outbound/amocrm/webhooks.py`**

```python
import asyncio
from collections.abc import Awaitable, Callable

import httpx

from app.adapters.outbound.amocrm.client import AmoCRMClient
from app.domain.errors import CRMUnavailable

WEBHOOK_EVENTS = ["note_lead"]
WEBHOOK_PATH_PREFIX = "/integrations/amocrm/webhook/"


def webhook_destination(public_url: str, secret: str) -> str:
    return f"{public_url.rstrip('/')}{WEBHOOK_PATH_PREFIX}{secret}"


class AmoCRMWebhookRegistrar:
    def __init__(self, client: AmoCRMClient) -> None:
        self._client = client

    async def register(self, destination: str) -> None:
        await self._client.post(
            "/webhooks", {"destination": destination, "settings": WEBHOOK_EVENTS, "sort": 10}
        )

    async def unregister(self, destination: str) -> None:
        await self._client.delete("/webhooks", {"destination": destination})


async def resolve_tunnel_url(
    http: httpx.AsyncClient,
    metrics_url: str,
    *,
    attempts: int = 15,
    delay_s: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    # Quick tunnel получает случайный адрес при каждом запуске — узнаём его у cloudflared
    for _ in range(attempts):
        try:
            response = await http.get(metrics_url)
            if response.status_code == httpx.codes.OK:
                hostname = response.json().get("hostname")
                if hostname:
                    return f"https://{hostname}"
        except (httpx.HTTPError, ValueError):
            pass
        await sleep(delay_s)
    raise CRMUnavailable("cloudflared tunnel did not report a public hostname")
```

- [ ] **Step 4: PASS + Commit**

Run: `poetry run pytest -q && poetry run ruff check . && poetry run mypy app`

```bash
git add app/adapters/outbound/amocrm/webhooks.py tests/unit/test_amocrm_webhooks.py
git commit -m "feat: register AmoCRM webhook at the tunnel address"
```

---

### Task 7: Вебхук-эндпоинт, защита туннеля, маскирование логов

**Files:**
- Create: `app/adapters/inbound/http/amocrm.py`, `app/adapters/inbound/http/log_filters.py`, `tests/api/test_amocrm_webhook.py`, `tests/unit/test_log_filters.py`
- Modify: `app/adapters/inbound/http/security.py`, `app/adapters/inbound/http/errors.py`, `app/adapters/inbound/http/app.py`, `app/container.py`, `tests/api/conftest.py`

**Interfaces:**
- Consumes: `HandleIncomingMessageUseCase.execute(lead_id, message_id)`, `WEBHOOK_PATH_PREFIX`, `CRMUnavailable`, `Settings.amocrm_*`
- Produces:
  - `Container.handle_incoming: HandleIncomingMessageUseCase | None = None`
  - `parse_note_events(form: Iterable[tuple[str, str]]) -> list[tuple[str, str]]` (пары `(lead_id, note_id)`)
  - `build_amocrm_router(settings, limiter) -> APIRouter`
  - `TunnelGuardMiddleware(app, allowed_prefix)`, `client_ip(request) -> str`
  - `MaskWebhookSecretFilter`, `install_log_filters() -> None`

- [ ] **Step 1: Контейнер**

`app/container.py` — добавить поле (импорт `HandleIncomingMessageUseCase`):

```python
    handle_incoming: HandleIncomingMessageUseCase | None = None
```

- [ ] **Step 2: Падающие тесты**

`tests/unit/test_log_filters.py`:

```python
import logging

from app.adapters.inbound.http.log_filters import MaskWebhookSecretFilter


def test_access_log_masks_webhook_secret() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", "/integrations/amocrm/webhook/SUPERSECRET?x=1", "1.1", 200),
        None,
    )
    assert MaskWebhookSecretFilter().filter(record) is True
    message = record.getMessage()
    assert "SUPERSECRET" not in message
    assert "/integrations/amocrm/webhook/***" in message
```

`tests/api/test_amocrm_webhook.py`:

```python
import logging
from urllib.parse import urlencode

from cryptography.fernet import Fernet

from app.domain.errors import CRMUnavailable
from tests.api.conftest import API_KEY, make_client, make_container, make_settings

SECRET = "w" * 32
HOOK = f"/integrations/amocrm/webhook/{SECRET}"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def amocrm_settings(**overrides: object):  # type: ignore[no-untyped-def]
    return make_settings(
        crm_provider="amocrm",
        amocrm_subdomain="demo",
        amocrm_client_id="cid",
        amocrm_client_secret="csecret",
        amocrm_token_key=Fernet.generate_key().decode(),
        amocrm_webhook_secret=SECRET,
        **overrides,
    )


class RecordingHandler:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    async def execute(self, lead_id: str, message_id: str) -> str:
        self.calls.append((lead_id, message_id))
        if self.fail:
            raise RuntimeError("boom")
        return "processed"


def payload(*notes: tuple[int, int], subdomain: str = "demo") -> str:
    fields: dict[str, str] = {"account[subdomain]": subdomain, "account[id]": "1"}
    for i, (lead_id, note_id) in enumerate(notes):
        fields[f"leads[note][{i}][note][id]"] = str(note_id)
        fields[f"leads[note][{i}][note][element_id]"] = str(lead_id)
        fields[f"leads[note][{i}][note][note_type]"] = "102"
    return urlencode(fields)


async def container_with(handler: RecordingHandler):  # type: ignore[no-untyped-def]
    container = await make_container()
    return container.__class__(
        answer_inquiry=container.answer_inquiry,
        ingest_knowledge=container.ingest_knowledge,
        crm=container.crm,
        knowledge=container.knowledge,
        handle_incoming=handler,  # type: ignore[arg-type]
    )


async def test_batched_webhook_schedules_every_note() -> None:
    handler = RecordingHandler()
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        response = await client.post(HOOK, content=payload((7, 101), (8, 102)), headers=FORM)
    assert response.status_code == 200
    assert response.json() == {"accepted": 2}
    assert handler.calls == [("7", "101"), ("8", "102")]


async def test_wrong_secret_is_404() -> None:
    handler = RecordingHandler()
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        response = await client.post(
            "/integrations/amocrm/webhook/" + "x" * 32, content=payload((7, 1)), headers=FORM
        )
    assert response.status_code == 404
    assert handler.calls == []


async def test_foreign_account_is_403() -> None:
    handler = RecordingHandler()
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        response = await client.post(
            HOOK, content=payload((7, 1), subdomain="evil"), headers=FORM
        )
    assert response.status_code == 403
    assert handler.calls == []


async def test_handler_failure_does_not_break_webhook(caplog) -> None:  # type: ignore[no-untyped-def]
    handler = RecordingHandler(fail=True)
    with caplog.at_level(logging.ERROR):
        async with make_client(amocrm_settings(), await container_with(handler)) as client:
            response = await client.post(HOOK, content=payload((7, 1)), headers=FORM)
    assert response.status_code == 200
    assert "amocrm note handling failed" in caplog.text


async def test_webhook_route_absent_in_mock_mode() -> None:
    async with make_client() as client:
        response = await client.post(HOOK, content=payload((7, 1)), headers=FORM)
    assert response.status_code == 404


async def test_tunnel_requests_reach_only_the_webhook() -> None:
    handler = RecordingHandler()
    tunnel = {"Cf-Connecting-Ip": "203.0.113.5", "Cf-Ray": "abc"}
    async with make_client(amocrm_settings(), await container_with(handler)) as client:
        api = await client.get("/api/v1/leads", headers={**tunnel, "X-API-Key": API_KEY})
        page = await client.get("/", headers=tunnel)
        hook = await client.post(HOOK, content=payload((7, 1)), headers={**FORM, **tunnel})
    assert api.status_code == page.status_code == 404
    assert hook.status_code == 200


async def test_webhook_rate_limit_is_per_cloudflare_client() -> None:
    handler = RecordingHandler()
    settings = amocrm_settings(amocrm_webhook_rate_limit="1/minute")
    async with make_client(settings, await container_with(handler)) as client:

        async def send(ip: str) -> int:
            headers = {**FORM, "Cf-Connecting-Ip": ip}
            return (await client.post(HOOK, content=payload((7, 1)), headers=headers)).status_code

        assert [await send("203.0.113.1"), await send("203.0.113.2"), await send("203.0.113.1")] == [
            200,
            200,
            429,
        ]


async def test_crm_outage_on_api_is_503() -> None:
    container = await make_container()

    async def broken(_: str) -> None:
        raise CRMUnavailable("amocrm down")

    container.crm.get_lead = broken  # type: ignore[method-assign]
    async with make_client(container=container) as client:
        response = await client.post(
            "/api/v1/inquiries",
            json={"lead_id": "lead-1", "message": "Как принимать?"},
            headers={"X-API-Key": API_KEY},
        )
    assert response.status_code == 503
    assert "amocrm down" not in response.text
```

- [ ] **Step 3: FAIL**

Run: `poetry run pytest tests/api/test_amocrm_webhook.py tests/unit/test_log_filters.py -q`
Expected: FAIL — нет модулей и роута.

- [ ] **Step 4: Реализация**

`app/adapters/inbound/http/log_filters.py`:

```python
import logging
import re

from app.adapters.outbound.amocrm.webhooks import WEBHOOK_PATH_PREFIX

_SECRET_IN_PATH = re.compile(rf"({re.escape(WEBHOOK_PATH_PREFIX)})[^/?\s]+")


class MaskWebhookSecretFilter(logging.Filter):
    """Секрет вебхука живёт в пути URL, а uvicorn пишет путь в access-лог."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SECRET_IN_PATH.sub(r"\1***", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


def install_log_filters() -> None:
    logging.getLogger("uvicorn.access").addFilter(MaskWebhookSecretFilter())
```

`app/adapters/inbound/http/security.py` — добавить (импорты `Request` из `starlette.requests`, `get_remote_address` из `slowapi.util`):

```python
_TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray")


def client_ip(request: Request) -> str:
    # За туннелем все запросы приходят с адреса cloudflared — настоящий IP в заголовке Cloudflare
    return request.headers.get("cf-connecting-ip") or get_remote_address(request)


class TunnelGuardMiddleware:
    """Через публичный туннель доступен только вебхук: демо-страница и API остаются локальными."""

    def __init__(self, app: ASGIApp, allowed_prefix: str) -> None:
        self.app = app
        self.allowed_prefix = allowed_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not scope["path"].startswith(self.allowed_prefix):
            headers = Headers(scope=scope)
            if any(name in headers for name in _TUNNEL_HEADERS):
                request_id = scope.get("state", {}).get("request_id")
                response = JSONResponse(
                    status_code=404, content={"detail": "Not Found", "request_id": request_id}
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
```

`app/adapters/inbound/http/errors.py` — добавить обработчик (импорт `CRMUnavailable`):

```python
async def _crm_unavailable(request: Request, exc: Exception) -> JSONResponse:
    logger.error("CRM unavailable: %s", exc)
    return error_response(request, 503, "CRM временно недоступна, попробуйте позже")
```

и в `install_error_handlers`: `app.add_exception_handler(CRMUnavailable, _crm_unavailable)`.

`app/adapters/inbound/http/amocrm.py`:

```python
import logging
import re
import secrets
from collections.abc import Iterable
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from slowapi import Limiter

from app.adapters.inbound.http.routes import get_container
from app.adapters.outbound.amocrm.webhooks import WEBHOOK_PATH_PREFIX
from app.application.handle_incoming import HandleIncomingMessageUseCase
from app.config import Settings

logger = logging.getLogger(__name__)

_NOTE_FIELD = re.compile(r"^leads\[note\]\[(\d+)\]\[note\]\[(id|element_id)\]$")


def parse_note_events(form: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Пары (lead_id, note_id) из form-payload вебхука note_lead; AmoCRM шлёт их пачкой."""
    grouped: dict[int, dict[str, str]] = {}
    for key, value in form:
        match = _NOTE_FIELD.match(key)
        if match and value.isdigit():
            grouped.setdefault(int(match.group(1)), {})[match.group(2)] = value
    return [
        (fields["element_id"], fields["id"])
        for _, fields in sorted(grouped.items())
        if "id" in fields and "element_id" in fields
    ]


def build_amocrm_router(settings: Settings, limiter: Limiter) -> APIRouter:
    if settings.amocrm_webhook_secret is None:
        raise RuntimeError("AMOCRM_WEBHOOK_SECRET is not set")
    expected_secret = settings.amocrm_webhook_secret.get_secret_value().encode()
    router = APIRouter()

    @router.post(WEBHOOK_PATH_PREFIX + "{token}", include_in_schema=False)
    @limiter.limit(settings.amocrm_webhook_rate_limit)
    async def amocrm_webhook(
        request: Request, token: str, background: BackgroundTasks
    ) -> dict[str, int]:
        if not secrets.compare_digest(token.encode(), expected_secret):
            raise HTTPException(status_code=404, detail="Not Found")
        form = parse_qsl((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
        if dict(form).get("account[subdomain]") != settings.amocrm_subdomain:
            raise HTTPException(status_code=403, detail="Вебхук от другого аккаунта AmoCRM")
        use_case = get_container(request).handle_incoming
        if use_case is None:
            raise HTTPException(status_code=404, detail="Not Found")
        events = parse_note_events(form)
        # AmoCRM ждёт ответ за пару секунд, а генерация идёт дольше — обрабатываем после ответа
        for lead_id, note_id in events:
            background.add_task(_handle_safely, use_case, lead_id, note_id)
        return {"accepted": len(events)}

    return router


async def _handle_safely(
    use_case: HandleIncomingMessageUseCase, lead_id: str, note_id: str
) -> None:
    try:
        result = await use_case.execute(lead_id, note_id)
        logger.info("amocrm note processed lead_id=%s note_id=%s result=%s", lead_id, note_id, result)
    except Exception:
        logger.exception("amocrm note handling failed lead_id=%s note_id=%s", lead_id, note_id)
```

`app/adapters/inbound/http/app.py`:
- импорт `client_ip`, `TunnelGuardMiddleware` из `security`, `build_amocrm_router` из `amocrm`, `WEBHOOK_PATH_PREFIX` из `app.adapters.outbound.amocrm.webhooks`;
- `limiter = Limiter(key_func=client_ip)`;
- после `app.include_router(build_api_router(...))`:

```python
    if settings.crm_provider == "amocrm":
        app.include_router(build_amocrm_router(settings, limiter))
```

- middleware (порядок: добавленный последним — внешний):

```python
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(TunnelGuardMiddleware, allowed_prefix=WEBHOOK_PATH_PREFIX)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
```

`tests/api/conftest.py` — `make_client(settings, container)` уже принимает оба аргумента позиционно; изменений не требуется.

- [ ] **Step 5: PASS**

Run: `poetry run pytest -q && poetry run ruff check . && poetry run ruff format --check . && poetry run mypy app`
Expected: всё зелёное, включая старые API-тесты (rate limit по `client_ip` без заголовка Cloudflare = прежнее поведение).

- [ ] **Step 6: Commit**

```bash
git add app/adapters/inbound/http app/container.py tests/api/test_amocrm_webhook.py tests/unit/test_log_filters.py
git commit -m "feat: accept AmoCRM webhooks and restrict tunnel traffic"
```

---

### Task 8: Сборка зависимостей, регистрация вебхука при старте, CLI

**Files:**
- Create: `app/adapters/outbound/amocrm/tools.py`
- Modify: `app/bootstrap.py`, `app/container.py`, `app/main.py`, `app/adapters/inbound/cli.py`
- Test: `tests/unit/test_bootstrap.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: всё из задач 1–7
- Produces:
  - `AmoCRMTools(http: httpx.AsyncClient, oauth: AmoCRMOAuth, gateway: AmoCRMGateway, registrar: AmoCRMWebhookRegistrar)`
  - `Container.amocrm: AmoCRMTools | None = None`
  - `amocrm_webhook_registration(tools: AmoCRMTools, settings: Settings) -> AbstractAsyncContextManager[None]`
  - CLI: `amocrm-auth <code>`, `amocrm-seed`, `amocrm-say <lead_id> <text>`

- [ ] **Step 1: Падающие тесты**

Дописать в `tests/unit/test_bootstrap.py`:

```python
from cryptography.fernet import Fernet
import httpx
import respx

from app.adapters.outbound.amocrm.gateway import AmoCRMGateway
from app.application.handle_incoming import HandleIncomingMessageUseCase
from app.bootstrap import amocrm_webhook_registration


def amocrm(**overrides: object) -> Settings:
    return make(
        llm_provider="fake",
        embedding_provider="fake",
        crm_provider="amocrm",
        amocrm_subdomain="demo",
        amocrm_client_id="cid",
        amocrm_client_secret="csecret",
        amocrm_token_key=Fernet.generate_key().decode(),
        amocrm_webhook_secret="w" * 32,
        **overrides,
    )


async def test_amocrm_provider_wires_gateway_and_handler() -> None:
    async with build_container(amocrm()) as container:
        assert isinstance(container.crm, AmoCRMGateway)
        assert isinstance(container.handle_incoming, HandleIncomingMessageUseCase)
        assert container.amocrm is not None


async def test_mock_provider_has_no_amocrm_parts() -> None:
    async with build_container(make(llm_provider="fake", embedding_provider="fake")) as c:
        assert c.handle_incoming is None
        assert c.amocrm is None


async def test_webhook_is_registered_and_removed(respx_mock: respx.MockRouter) -> None:
    settings = amocrm(amocrm_tunnel_metrics_url="http://tunnel:2000/quicktunnel")
    respx_mock.get("http://tunnel:2000/quicktunnel").mock(
        return_value=httpx.Response(200, json={"hostname": "abc.trycloudflare.com"})
    )
    register = respx_mock.post("https://demo.amocrm.ru/api/v4/webhooks").mock(
        return_value=httpx.Response(201, json={})
    )
    unregister = respx_mock.delete("https://demo.amocrm.ru/api/v4/webhooks").mock(
        return_value=httpx.Response(204)
    )
    async with build_container(settings) as container:
        assert container.amocrm is not None
        container.amocrm.oauth._cached = TokenPair("t", "r", 1e12)  # type: ignore[attr-defined]
        async with amocrm_webhook_registration(container.amocrm, settings):
            assert register.call_count == 1
        assert unregister.call_count == 1
    destination = json.loads(register.calls[0].request.content)["destination"]
    assert destination == "https://abc.trycloudflare.com/integrations/amocrm/webhook/" + "w" * 32


async def test_registration_failure_does_not_stop_startup(respx_mock: respx.MockRouter) -> None:
    settings = amocrm(amocrm_tunnel_metrics_url="http://tunnel:2000/quicktunnel")
    respx_mock.get("http://tunnel:2000/quicktunnel").mock(return_value=httpx.Response(503))
    async with build_container(settings) as container:
        assert container.amocrm is not None
        async with amocrm_webhook_registration(container.amocrm, settings, attempts=1):
            pass
```

(в начало файла: `import json`, `from app.adapters.outbound.amocrm.tokens import TokenPair`.)

Дописать в `tests/unit/test_cli.py`:

```python
from app.domain.models import Role


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
        return "1"


def patch_amocrm(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeOAuth, FakeGateway]:
    oauth, gateway = FakeOAuth(), FakeGateway()

    class Tools:
        pass

    tools = Tools()
    tools.oauth = oauth  # type: ignore[attr-defined]
    tools.gateway = gateway  # type: ignore[attr-defined]

    class FakeContainer:
        amocrm = tools

    @asynccontextmanager
    async def fake_build(_: Settings) -> AsyncIterator[FakeContainer]:
        yield FakeContainer()

    monkeypatch.setattr(cli, "build_container", fake_build)
    monkeypatch.setenv("APP_API_KEY", "test-api-key-0123456789abcdef")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return oauth, gateway


def test_amocrm_auth_exchanges_code(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth, _ = patch_amocrm(monkeypatch)
    assert cli.main(["amocrm-auth", "the-code"]) == 0
    assert oauth.codes == ["the-code"]


def test_amocrm_say_adds_client_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _, gateway = patch_amocrm(monkeypatch)
    assert cli.main(["amocrm-say", "123", "Как принимать?"]) == 0
    assert gateway.messages == [("123", Role.CLIENT, "Как принимать?")]


def test_amocrm_seed_creates_demo_leads_with_history(monkeypatch: pytest.MonkeyPatch) -> None:
    _, gateway = patch_amocrm(monkeypatch)
    assert cli.main(["amocrm-seed"]) == 0
    assert len(gateway.leads) == 3
    assert gateway.messages[0][1] is Role.CLIENT
    assert len(gateway.messages) == 9


def test_amocrm_commands_require_amocrm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeContainer:
        amocrm = None

    @asynccontextmanager
    async def fake_build(_: Settings) -> AsyncIterator[FakeContainer]:
        yield FakeContainer()

    monkeypatch.setattr(cli, "build_container", fake_build)
    monkeypatch.setenv("APP_API_KEY", "test-api-key-0123456789abcdef")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert cli.main(["amocrm-say", "1", "x"]) == 1
```

- [ ] **Step 2: FAIL**

Run: `poetry run pytest tests/unit/test_bootstrap.py tests/unit/test_cli.py -q`
Expected: FAIL — нет `amocrm_webhook_registration`, полей контейнера и команд.

- [ ] **Step 3: Реализация**

`app/adapters/outbound/amocrm/tools.py`:

```python
from dataclasses import dataclass

import httpx

from app.adapters.outbound.amocrm.gateway import AmoCRMGateway
from app.adapters.outbound.amocrm.oauth import AmoCRMOAuth
from app.adapters.outbound.amocrm.webhooks import AmoCRMWebhookRegistrar


@dataclass(frozen=True, slots=True)
class AmoCRMTools:
    """Части интеграции, нужные вне use case'ов: CLI и регистрация вебхука."""

    http: httpx.AsyncClient
    oauth: AmoCRMOAuth
    gateway: AmoCRMGateway
    registrar: AmoCRMWebhookRegistrar
```

`app/container.py` — поле `amocrm: AmoCRMTools | None = None` (импорт `AmoCRMTools`).

`app/bootstrap.py` — добавить (импорты `logging`, `AsyncIterator`, `AmoCRMClient`, `AmoCRMGateway`, `AmoCRMOAuth`, `PgTokenStore`, `AmoCRMTools`, `AmoCRMWebhookRegistrar`, `resolve_tunnel_url`, `webhook_destination`, `PgProcessedEvents`, `HandleIncomingMessageUseCase`, `CRMGateway`, `DomainError`):

```python
logger = logging.getLogger(__name__)


def _amocrm_tools(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], stack: AsyncExitStack
) -> AmoCRMTools:
    assert settings.amocrm_subdomain and settings.amocrm_client_id  # noqa: S101 — проверено валидатором
    assert settings.amocrm_client_secret and settings.amocrm_token_key  # noqa: S101
    http = httpx.AsyncClient(timeout=settings.amocrm_timeout_s)
    stack.push_async_callback(http.aclose)
    oauth = AmoCRMOAuth(
        http,
        subdomain=settings.amocrm_subdomain,
        client_id=settings.amocrm_client_id,
        client_secret=settings.amocrm_client_secret,
        redirect_uri=settings.amocrm_redirect_uri,
        store=PgTokenStore(sessionmaker, settings.amocrm_token_key),
    )
    client = AmoCRMClient(http, settings.amocrm_subdomain, oauth)
    return AmoCRMTools(
        http=http,
        oauth=oauth,
        gateway=AmoCRMGateway(client),
        registrar=AmoCRMWebhookRegistrar(client),
    )


@asynccontextmanager
async def amocrm_webhook_registration(
    tools: AmoCRMTools, settings: Settings, *, attempts: int = 15
) -> AsyncIterator[None]:
    destination: str | None = None
    if settings.amocrm_tunnel_metrics_url and settings.amocrm_webhook_secret:
        try:
            public_url = await resolve_tunnel_url(
                tools.http, settings.amocrm_tunnel_metrics_url, attempts=attempts
            )
            destination = webhook_destination(
                public_url, settings.amocrm_webhook_secret.get_secret_value()
            )
            await tools.registrar.register(destination)
            logger.info("AmoCRM webhook registered at %s", public_url)
        except DomainError as exc:
            destination = None
            logger.warning("AmoCRM webhook registration skipped: %s", exc)
    try:
        yield
    finally:
        if destination is not None:
            try:
                await tools.registrar.unregister(destination)
            except DomainError as exc:
                logger.warning("AmoCRM webhook was not removed: %s", exc)
```

В `build_container`: заменить создание `crm` и сборку контейнера:

```python
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        amocrm: AmoCRMTools | None = None
        crm: CRMGateway
        if settings.crm_provider == "amocrm":
            amocrm = _amocrm_tools(settings, sessionmaker, stack)
            crm = amocrm.gateway
        else:
            crm = MockCRMGateway.from_json_file(settings.crm_fixture_path)
```

(`knowledge` создаётся через тот же `sessionmaker`.) После создания `answer_inquiry`:

```python
        answer_inquiry = AnswerInquiryUseCase(
            crm,
            embedder,
            knowledge,
            llm,
            retrieval_limit=settings.retrieval_limit,
            min_score=min_score,
            dialog_max_messages=settings.dialog_max_messages,
        )
        handle_incoming = None
        if amocrm is not None:
            handle_incoming = HandleIncomingMessageUseCase(
                crm=crm,
                answer_inquiry=answer_inquiry,
                publisher=amocrm.gateway,
                processed_events=PgProcessedEvents(sessionmaker),
            )

        yield Container(
            answer_inquiry=answer_inquiry,
            ingest_knowledge=IngestKnowledgeUseCase(
                knowledge,
                embedder,
                embedding_model=embedding_model,
                embedding_dim=settings.embedding_dim,
            ),
            crm=crm,
            knowledge=knowledge,
            handle_incoming=handle_incoming,
            amocrm=amocrm,
        )
```

`app/main.py`:

```python
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from app.adapters.inbound.http.app import create_app
from app.adapters.inbound.http.log_filters import install_log_filters
from app.bootstrap import amocrm_webhook_registration, build_container, warm_up
from app.config import Settings
from app.container import Container

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
install_log_filters()


@asynccontextmanager
async def _app_container(settings: Settings) -> AsyncIterator[Container]:
    async with AsyncExitStack() as stack:
        container = await stack.enter_async_context(build_container(settings))
        await warm_up(container.answer_inquiry.embedder, settings)
        if container.amocrm is not None:
            await stack.enter_async_context(
                amocrm_webhook_registration(container.amocrm, settings)
            )
        yield container


app = create_app(Settings(), _app_container)
```

`app/adapters/inbound/cli.py` — заменить `main` и добавить команды (импорты `Role`, `MockCRMGateway`, `Awaitable`, `Callable`, `Any`):

```python
class _AmoCRMNotConfigured(DomainError):
    pass


async def _with_amocrm(settings: Settings, action: Callable[[Any], Awaitable[None]]) -> None:
    async with build_container(settings) as container:
        if container.amocrm is None:
            raise _AmoCRMNotConfigured("set CRM_PROVIDER=amocrm and AMOCRM_* in .env")
        await action(container.amocrm)


async def _amocrm_seed(settings: Settings, tools: Any) -> None:
    demo = await MockCRMGateway.from_json_file(settings.crm_fixture_path).list_leads()
    for lead in demo:
        lead_id = await tools.gateway.create_lead(lead.name)
        for message in lead.dialog:
            await tools.gateway.add_message(lead_id, message.role, message.text)
        print(f"{lead_id}\t{lead.name}")  # noqa: T201 — вывод команды для пользователя


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inquiry-assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="load data/kb/*.md into the vector store")
    auth = commands.add_parser("amocrm-auth", help="exchange AmoCRM authorization code")
    auth.add_argument("code")
    commands.add_parser("amocrm-seed", help="create demo leads with dialog history")
    say = commands.add_parser("amocrm-say", help="add an incoming client message to a lead")
    say.add_argument("lead_id")
    say.add_argument("text")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    try:
        if args.command == "ingest":
            asyncio.run(_ingest(settings))
        elif args.command == "amocrm-auth":
            asyncio.run(_with_amocrm(settings, lambda t: t.oauth.exchange_code(args.code)))
            logger.info("AmoCRM authorized")
        elif args.command == "amocrm-seed":
            asyncio.run(_with_amocrm(settings, lambda t: _amocrm_seed(settings, t)))
        elif args.command == "amocrm-say":
            asyncio.run(
                _with_amocrm(
                    settings, lambda t: t.gateway.add_message(args.lead_id, Role.CLIENT, args.text)
                )
            )
    except DomainError as exc:
        logger.error("%s failed: %s", args.command, exc)
        return 1
    return 0
```

Примечание: `lambda t: t.gateway.add_message(...)` возвращает корутину `str`, что совместимо с `Awaitable`; `_with_amocrm` её ожидает.

- [ ] **Step 4: PASS**

Run: `poetry run pytest -q && poetry run ruff check . && poetry run ruff format --check . && poetry run mypy app`
Если ruff ругается на `print` (T201 не включён — не будет) или `assert` (S101) — `noqa` уже стоит.

- [ ] **Step 5: Commit**

```bash
git add app tests/unit/test_bootstrap.py tests/unit/test_cli.py
git commit -m "feat: wire AmoCRM integration, webhook registration and CLI"
```

---

### Task 9: Docker, Makefile, документация

**Files:**
- Modify: `docker-compose.yml`, `Makefile`, `.env.example`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: Сервис туннеля**

`docker-compose.yml` — добавить сервис:

```yaml
  tunnel:
    profiles: ["amocrm"]
    image: cloudflare/cloudflared:latest
    command: ["tunnel", "--no-autoupdate", "--metrics", "0.0.0.0:2000", "--url", "http://app:8000"]
    depends_on:
      - app
    restart: unless-stopped
```

- [ ] **Step 2: Makefile**

Добавить в `.PHONY` и цели:

```makefile
up-amocrm:
	docker compose --profile amocrm up --build -d

amocrm-auth:
	docker compose exec app python -m app.adapters.inbound.cli amocrm-auth "$(code)"

amocrm-seed:
	docker compose exec app python -m app.adapters.inbound.cli amocrm-seed

client-says:
	docker compose exec app python -m app.adapters.inbound.cli amocrm-say "$(lead)" "$(text)"
```

и `down` заменить на `docker compose --profile amocrm down` (иначе туннель остаётся жить).

- [ ] **Step 3: `.env.example`**

```dotenv
# mock | amocrm
CRM_PROVIDER=mock
# Для CRM_PROVIDER=amocrm (внешняя интеграция AmoCRM, «Ключи и доступы»)
AMOCRM_SUBDOMAIN=
AMOCRM_CLIENT_ID=
AMOCRM_CLIENT_SECRET=
AMOCRM_REDIRECT_URI=https://example.com
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
AMOCRM_TOKEN_KEY=
# python -c "import secrets; print(secrets.token_urlsafe(32))"
AMOCRM_WEBHOOK_SECRET=
AMOCRM_TUNNEL_METRICS_URL=http://tunnel:2000/quicktunnel
```

- [ ] **Step 4: Локальный `.env`**

Дописать `AMOCRM_TOKEN_KEY` и `AMOCRM_WEBHOOK_SECRET` (сгенерировать командами выше), `AMOCRM_REDIRECT_URI=https://example.com`, `AMOCRM_TUNNEL_METRICS_URL=http://tunnel:2000/quicktunnel`, `CRM_PROVIDER=amocrm`. Значения в чат не выводить.

- [ ] **Step 5: README и CLAUDE.md**

README — новый раздел «Интеграция с AmoCRM» после «API»:
- схема: `sms_in` в сделке → вебхук `note_lead` через cloudflared → `HandleIncomingMessageUseCase` → `service_message`;
- настройка за 4 шага: внешняя интеграция в AmoCRM (redirect `https://example.com`) → `.env` → `make up-amocrm` → `make amocrm-auth code=…` (код из карточки интеграции живёт 20 минут) → `make amocrm-seed` → `make client-says lead=… text="…"`;
- безопасность: секрет в пути + проверка поддомена + содержимое только из API; токены зашифрованы Fernet; через туннель доступен только вебхук; секрет маскируется в логах;
- ограничения: at-most-once обработка (для гарантий — очередь), quick tunnel меняет адрес при перезапуске (вебхук перерегистрируется автоматически), Chats API мессенджеров не используется.

В таблицу провайдеров добавить `CRM_PROVIDER | mock, amocrm | mock`. В «Структуру» — `adapters/outbound/amocrm`.

CLAUDE.md — в «Команды» добавить `make up-amocrm`, `make amocrm-auth code=…`, `make amocrm-seed`, `make client-says lead=… text=…`; в «Инварианты» — «ассистент реагирует только на `sms_in`, пишет `service_message`; вебхук обрабатывается после ответа 200».

- [ ] **Step 6: Проверка и коммит**

Run: `make test && make test-integration && make lint`

```bash
git add docker-compose.yml Makefile .env.example README.md CLAUDE.md
git commit -m "docs: document AmoCRM integration and add tunnel service"
```

---

### Task 10: Живая проверка на аккаунте AmoCRM

**Files:**
- Modify (по результатам): `app/adapters/inbound/http/amocrm.py`, `tests/api/test_amocrm_webhook.py`

- [ ] **Step 1: Поднять стек**

Run: `make up-amocrm && sleep 20 && docker compose logs app | grep -E "AmoCRM|webhook" | tail`
Expected: `AmoCRM webhook registration skipped: AmoCRM is not authorized` (ещё нет токенов) или `registered`.

- [ ] **Step 2: Авторизация**

Попросить пользователя: в карточке интеграции AmoCRM → «Ключи и доступы» скопировать свежий «Код авторизации» и выполнить `! make amocrm-auth code=<код>` (код в чат не присылать). Затем `docker compose restart app` и проверить в логах `AmoCRM webhook registered at https://….trycloudflare.com`.

- [ ] **Step 3: Проверка туннеля и заголовков Cloudflare**

```bash
HOST=$(docker compose logs tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)
curl -s -o /dev/null -w "page via tunnel: %{http_code}\n" "$HOST/"
curl -s -o /dev/null -w "api via tunnel: %{http_code}\n" "$HOST/api/v1/leads"
```
Expected: 404 и 404 (TunnelGuard видит `Cf-Connecting-Ip`). Если 200 — заголовков нет: переключить защиту на проверку `Host` (`*.trycloudflare.com`) с тестом, ruling в журнал.

- [ ] **Step 4: Демо-сделки и сообщение клиента**

```bash
make amocrm-seed
make client-says lead=<id Марины> text="Подскажите, как принимать цеолит и можно ли совмещать с чем-то ещё?"
sleep 15; docker compose logs app | grep -E "amocrm note|HTTP Request: POST https://.*amocrm" | tail
```
Expected: `amocrm note processed ... result=processed`; в сделке в AmoCRM — примечание «Ассистент O-complex». Попросить пользователя подтвердить, что примечание видно в ленте сделки.

- [ ] **Step 5: Реальный формат payload**

Если в логах `accepted: 0` или обработка не запустилась — временно залогировать ключи form-payload (без значений), сохранить реальный пример в тест `test_real_amocrm_payload_is_parsed` в `tests/api/test_amocrm_webhook.py`, поправить `parse_note_events` (RED→GREEN), убрать временный лог. Ruling в журнал.

- [ ] **Step 6: Три сделки — разные подсказки**

Повторить `client-says` для Анны и Игоря тем же вопросом; убедиться, что подсказки различаются; проверить повторную доставку (ручной повтор POST того же payload в `$HOST/integrations/amocrm/webhook/<секрет>` → `result=duplicate`).

- [ ] **Step 7: Финальная проверка и коммит**

Run: `poetry run pytest -q && make test-integration && make lint`

```bash
git add -A
git commit -m "test: pin real AmoCRM webhook payload format"   # только если были правки
```
