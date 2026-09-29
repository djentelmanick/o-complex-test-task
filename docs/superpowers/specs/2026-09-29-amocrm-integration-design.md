# Интеграция с AmoCRM: дизайн

Дата: 2026-09-29
Статус: на ревью
Основа: `2026-09-29-inquiry-assistant-design.md` (сервис-ассистент уже реализован)

## 1. Цель

Ассистент должен работать там, где работает менеджер, то есть в AmoCRM. Сообщение клиента появляется в сделке, и через несколько секунд в той же сделке появляется примечание с ответом клиенту, источниками и подсказкой по допродаже. Подсказка учитывает предыдущую переписку в сделке.

**Критерии успеха**

- `make up-amocrm` поднимает стек и туннель, вебхук регистрируется в AmoCRM без ручных шагов.
- `make amocrm-seed` создаёт три демо-сделки с историей (как mock-лиды).
- `make client-says lead=<id> text="…"` добавляет входящее сообщение, и через ≤ 15 с в сделке появляется примечание ассистента.
- У разных сделок разные подсказки: учитывается история примечаний `sms_in` / `sms_out`.
- Через туннель снаружи доступен только эндпоинт вебхука.
- `CRM_PROVIDER=mock` работает как раньше, без аккаунта AmoCRM.

**Вне скоупа**

- Чтение чатов мессенджеров (Chats API для интеграторов каналов).
- JS-виджет в интерфейсе AmoCRM.
- Очередь задач и гарантированная доставка (в README описано как следующий шаг).

## 2. Модель данных в AmoCRM

| Что | Тип примечания сделки | Роль в диалоге |
|---|---|---|
| Сообщение клиента | `sms_in` (`params.text`, `params.phone`) | `client`, триггер ассистента |
| Ответ менеджера | `sms_out` | `manager` |
| Результат ассистента | `service_message` (`params.service = "Ассистент O-complex"`, `params.text`) | не входит в диалог и не триггерит |
| Прочие (`common`, звонки и т.д.) | — | игнорируются |

Текст `service_message` выглядит так:

```
💬 Ответ клиенту:
<client_reply>

📚 Источники: <title1>, <title2>

💡 Подсказка менеджеру:
<manager_hint>
```

Если ответ fallback или LLM недоступна, текст такой: «Ассистент временно недоступен — ответьте клиенту вручную».

## 3. Изменения в ядре

- `DialogMessage` получает необязательное поле `id: str = ""`, чтобы найти сообщение-триггер.
- `AnswerInquiryUseCase.answer(message: str, dialog: Sequence[DialogMessage]) -> AssistantAnswer` — новый публичный метод. `execute(inquiry)` становится обёрткой: берёт лида и вызывает `answer(inquiry.message, lead.dialog)`.
- Новые порты (`application/ports.py`):
  - `AnswerPublisher.publish(lead_id: str, answer: AssistantAnswer) -> None`
  - `AnswerPublisher.publish_unavailable(lead_id: str) -> None`
  - `ProcessedEvents.first_seen(key: str) -> bool` (атомарно: `True`, если ключ новый)
- Новый use case `HandleIncomingMessageUseCase(crm, answer_inquiry, publisher, processed_events)`:
  - `execute(lead_id: str, message_id: str) -> HandleResult` (`processed | duplicate | ignored`).
  - Порядок: `first_seen(f"amocrm:note:{message_id}")`, если ключ уже был, возвращается `duplicate`. Затем `crm.get_lead(lead_id)`, в `lead.dialog` ищется сообщение с `id == message_id`. Если оно не найдено или его роль не `client`, результат `ignored`. Иначе история — сообщения до триггера, дальше `answer_inquiry.answer(...)` и `publisher.publish(...)`.
  - `LLMUnavailable` или `KnowledgeBaseUnavailable` приводят к `publisher.publish_unavailable(lead_id)`, затем исключение логируется.

## 4. Адаптеры

### 4.1 `adapters/outbound/amocrm/`

- **`oauth.py` — `AmoCRMOAuth`**
  - `exchange_code(code)`: `POST https://{subdomain}.amocrm.ru/oauth2/access_token` с `grant_type=authorization_code`.
  - `access_token()`: отдаёт токен из хранилища, при истечении (`expires_at − 60 с`) обновляет его через `grant_type=refresh_token`. Обновление идёт под `asyncio.Lock` и повторно проверяет срок после захвата lock. Новая пара сохраняется до того, как токен отдаётся вызывающему.
  - `invalidate()`: помечает access-токен истёкшим (используется при 401).
  - Ошибка `400 invalid_grant` при обновлении даёт `AmoCRMAuthError` с подсказкой выполнить `make amocrm-auth`.
- **`token_store.py` — `PgTokenStore`**
  - Таблица `amocrm_tokens(id smallint PK = 1, access_token bytea, refresh_token bytea, expires_at timestamptz, updated_at)`.
  - Шифрование Fernet (`cryptography`), ключ `AMOCRM_TOKEN_KEY`. Методы `load() -> TokenPair | None`, `save(TokenPair)`.
- **`client.py` — `AmoCRMClient`**
  - База `https://{subdomain}.amocrm.ru/api/v4`, заголовок `Authorization: Bearer`.
  - Повторы: при 401 один раз `invalidate` и повтор, при 429/5xx и сетевых ошибках до 2 повторов с backoff. Ответ 204 означает пустой список.
  - Ошибки превращаются в `CRMUnavailable` (новая доменная ошибка, HTTP 503).
- **`gateway.py` — `AmoCRMGateway`** реализует `CRMGateway` и `AnswerPublisher`:
  - `get_lead(id)`: `GET /leads/{id}` (имя) + `GET /leads/{id}/notes?filter[note_type][]=sms_in&filter[note_type][]=sms_out&order[id]=asc&limit=250` (с пагинацией). Ответ 404 даёт `LeadNotFound`.
  - `list_leads()`: 10 последних изменённых сделок (`GET /leads?order[updated_at]=desc&limit=10`), для каждой загружается диалог. Фильтр по тегу через API ненадёжен, а демо-сделки после seed и так оказываются последними.
  - `publish` / `publish_unavailable`: `POST /leads/{id}/notes` с типом `service_message`.
  - Идентификаторы сделок и примечаний — строки из целых чисел AmoCRM.
- **`webhooks.py` — `AmoCRMWebhookRegistrar`**
  - `register(destination)`: `POST /webhooks` с `settings: ["note_lead"]`.
  - `unregister(destination)`: `DELETE /webhooks` с `destination`.
- **`tunnel.py` — `resolve_tunnel_url(metrics_url)`**: `GET http://tunnel:2000/quicktunnel` отдаёт `{"hostname": "..."}`. До 30 с ждём, пока туннель поднимется.

### 4.2 `adapters/outbound/postgres/`

- `PgProcessedEvents.first_seen(key)`: `INSERT … ON CONFLICT DO NOTHING RETURNING key`.
- Таблица `processed_events(key text PK, created_at timestamptz default now())`.
- Миграция `0003_amocrm.py` создаёт `amocrm_tokens` и `processed_events`.

### 4.3 Inbound

- **HTTP `POST /integrations/amocrm/webhook/{secret}`** (`adapters/inbound/http/amocrm.py`):
  - Если `secret` не совпадает (`compare_digest`), ответ 404.
  - Тело `application/x-www-form-urlencoded` разбирается в плоский словарь. Если `account[subdomain]` не совпадает с `AMOCRM_SUBDOMAIN`, ответ 403.
  - Из ключей вида `leads[note][N][note][id]` / `…[element_id]` собираются пары `(lead_id, note_id)`. Точный формат сверяется с живым вебхуком на этапе реализации, парсер покрыт тестом на сохранённом реальном payload.
  - Для каждой пары запускается фоновая задача (`BackgroundTasks`) с `HandleIncomingMessageUseCase.execute`, ответ 200 `{"accepted": N}` уходит сразу.
  - Отдельный rate limit `AMOCRM_WEBHOOK_RATE_LIMIT` (по умолчанию `60/minute`).
  - Роут подключается только при `CRM_PROVIDER=amocrm`.
- **Lifespan** (при `CRM_PROVIDER=amocrm` и заданном `AMOCRM_TUNNEL_METRICS_URL`): получаем адрес туннеля и регистрируем `https://{host}/integrations/amocrm/webhook/{secret}`, при остановке снимаем регистрацию. Ошибка здесь только логируется, старт не блокируется.
- **CLI** (`python -m app.adapters.inbound.cli …`):
  - `amocrm-auth <code>` — обмен кода и сохранение токенов;
  - `amocrm-seed` — три сделки «Анна / Игорь / Марина» с тегом `o-complex-demo` и историей `sms_in`/`sms_out`, как в `data/crm_dialogs.json`; печатает их id;
  - `amocrm-say <lead_id> <text>` — примечание `sms_in` (имитация клиента).

### 4.4 Защита при работе через туннель

`TunnelGuardMiddleware`: если в запросе есть `Cf-Connecting-Ip` или `Cf-Ray` (их добавляет edge Cloudflare), разрешён только путь `/integrations/amocrm/webhook/…`, на всё остальное отвечаем 404. Локальные запросы идут как раньше. Rate limit по IP для туннельных запросов берёт `Cf-Connecting-Ip`, иначе все запросы через туннель попадали бы в одну корзину. Наличие заголовков проверяется на живом туннеле.

### 4.5 Логи

Фильтр для `uvicorn.access` маскирует сегмент пути после `/webhook/`: `…/webhook/***`.

## 5. Настройки

```
CRM_PROVIDER=mock|amocrm                  (mock)
AMOCRM_SUBDOMAIN                          имя без .amocrm.ru
AMOCRM_CLIENT_ID, AMOCRM_CLIENT_SECRET    SecretStr
AMOCRM_REDIRECT_URI                       (https://example.com), должен совпадать с интеграцией
AMOCRM_TOKEN_KEY                          Fernet-ключ, SecretStr
AMOCRM_WEBHOOK_SECRET                     ≥ 32 символов, SecretStr
AMOCRM_TUNNEL_METRICS_URL                 (http://tunnel:2000/quicktunnel)
AMOCRM_WEBHOOK_RATE_LIMIT                 (60/minute)
```

При `CRM_PROVIDER=amocrm` все `AMOCRM_*` без значений по умолчанию обязательны, иначе валидация настроек падает.

## 6. Инфраструктура

- `docker-compose.yml`: сервис `tunnel` (`cloudflare/cloudflared:latest`, `tunnel --no-autoupdate --metrics 0.0.0.0:2000 --url http://app:8000`), профиль `amocrm`, порты наружу не публикуются.
- `Makefile`: `up-amocrm`, `amocrm-auth code=…`, `amocrm-seed`, `client-says lead=… text=…`.
- Новая зависимость: `cryptography`.

## 7. Ошибки

| Ситуация | Поведение |
|---|---|
| Неверный секрет вебхука | 404, без логирования секрета |
| Чужой поддомен | 403 |
| Повторная доставка того же `note_id` | `duplicate`, ничего не пишем |
| Примечание не `sms_in` (в т.ч. наше) | `ignored` |
| GigaChat / база знаний недоступны | `service_message` «ассистент недоступен» + лог |
| AmoCRM 401 | обновить токен, повторить один раз |
| AmoCRM 429 / 5xx | до 2 повторов с backoff, затем `CRMUnavailable` в лог |
| Refresh-токен недействителен | `AmoCRMAuthError` в лог: «выполните make amocrm-auth» |
| Туннель не поднялся / регистрация не удалась | предупреждение в лог, сервис работает |
| `CRMUnavailable` в `/api/v1/*` | 503 |

## 8. Тестирование

- **Unit:** `HandleIncomingMessageUseCase` (processed, duplicate, ignored для не-`sms_in` и неизвестного id, обрезка истории, publish_unavailable при сбое LLM), `AnswerInquiryUseCase.answer`, форматирование текста примечания, разбор form-payload вебхука.
- **respx:** `AmoCRMOAuth` (обмен, обновление, конкурентное обновление, invalid_grant), `AmoCRMClient` (401→refresh, 429/5xx, 204), `AmoCRMGateway` (маппинг и фильтрация примечаний, пагинация, 404→LeadNotFound, publish), `AmoCRMWebhookRegistrar`, `resolve_tunnel_url`.
- **API:** вебхук (секрет, поддомен, 200 и фоновая задача, rate limit), `TunnelGuardMiddleware`, маскирование секрета в access-логе.
- **Integration (Postgres):** `PgTokenStore` (в БД нет открытого текста, сохранение и чтение), `PgProcessedEvents`, цикл миграций с `0003`.
- **Живая проверка:** `amocrm-auth`, `amocrm-seed`, `client-says` для трёх сделок; проверка примечаний и заголовков Cloudflare.
