## 1. Назначение проекта

`security-digest-bot` — Python-сервис, который по расписанию (по умолчанию раз в 4 часа):

1. Собирает новости по безопасности из RSS-лент (PHP, Linux, nginx, Node.js, JS, web-стек).
2. Фильтрует по ключевым словам, дедуплицирует по `link` через SQLite.
3. Отбирает только свежие новости и ограничивает их число за цикл (`MAX_ITEM_AGE_DAYS`, `MAX_ITEMS_PER_DIGEST`).
4. **Пропускает цикл**, если отобранных новостей меньше порога (`MIN_NEWS_THRESHOLD`).
5. Отправляет собранные новости в GigaChat, чтобы получить связный реферат (дайджест).
6. Публикует готовый дайджест в Telegram-канал.

Проект — **личный, некоммерческий**. GigaChat используется в режиме Freemium.

**Всё выполняется в Docker.** Локальный Python не нужен: запуск, тесты, линтер и
типизация работают в контейнерах. На хосте нужны только `docker` и `bash`;
единственная команда — `bash dbot.sh <команда>` (раздел 6).

---

## 2. Архитектура (pipeline)

```
[Scheduler: APScheduler]
        │
        ▼
[Collectors: RSS] ──► [Filter: keywords] ──► [Dedup: SQLite, filter_new]
                                                    │
                                                    ▼
                                          [Selection: свежесть + лимит]
                                                    │
                                                    ▼
                                          [Threshold check]
                                          (skip if < MIN_NEWS)
                                                    │
                                                    ▼
                                     [Summarizer: GigaChat]
                                                    │
                                                    ▼
                                     [Publisher: Telegram]
                                                    │
                                                    ▼
                                     [Dedup: mark_sent]
                                     (только после успешной публикации)
```

Ключевой принцип: **каждый этап — изолированный модуль с чётким контрактом** (вход/выход — dataclass `NewsItem` или `Digest`). Это позволяет тестировать этапы по отдельности и легко подменять реализацию (например, GigaChat → OpenAI-совместимый клиент).

---

## 3. Технологический стек

- **Python**: 3.11+
- **Планировщик**: APScheduler (BlockingScheduler) — цикл планировщика выполняется в главном потоке
- **HTTP / RSS**: `feedparser`, `httpx`
- **Telegram**: `aiogram` 3.x (async)
- **LLM**: официальный SDK `gigachat` (async-клиент `client.achat.create`; Freemium, модель по умолчанию `GigaChat-2-Max`)
- **Retry**: `tenacity` (`AsyncRetrying`, `wait_exponential`)
- **TLS**: `certifi` + встроенный корневой сертификат Минцифры (`ca_bundle.py`)
- **Хранилище**: SQLite (встроенный `sqlite3`, без ORM — состояние маленькое)
- **Конфиг**: `pydantic-settings` + `.env`
- **Тесты**: `pytest`, `pytest-asyncio`, `respx` (мок HTTP), фикстуры с сохранёнными RSS
- **Линт/формат**: `ruff` (lint + format), `mypy --strict`
- **Упаковка**: `pyproject.toml` (PEP 621), без `setup.py`
- **Среда запуска**: Docker, `docker compose` (multi-stage `docker/Dockerfile`: стадии `base`, `dev`, `runtime`)

---

## 4. Структура репозитория

```
security-digest-bot/
├── AGENTS.md
├── README.md
├── dbot.sh                    # единая точка входа: все команды через Docker
├── Makefile                   # опциональная обёртка над dbot.sh
├── docker-compose.yml         # сервисы: bot, once, dry-run, tools
├── pyproject.toml
├── .env.example
├── .gitignore
├── .gitattributes             # LF для *.sh (иначе ломается shebang на Windows)
├── .github/workflows/ci.yml   # lint + mypy + tests в Docker
├── docker/
│   ├── Dockerfile             # multi-stage: base / dev / runtime
│   └── entrypoint.sh          # отдаёт каталог данных appuser и сбрасывает root
├── scripts/
│   └── backup-db.sh           # бэкап SQLite через backup API
├── src/
│   └── digest_bot/
│       ├── __init__.py
│       ├── __main__.py        # `python -m digest_bot run [--once] [--dry-run]`
│       ├── config.py          # Settings (pydantic), фиды и ключевые слова по умолчанию
│       ├── main.py            # сборка зависимостей + APScheduler + обработка SIGTERM
│       ├── models.py          # NewsItem, Digest, FilterResult, CycleResult
│       ├── pipeline.py        # оркестратор: collect → filter → dedup → select → threshold → summarize → publish
│       ├── filters.py         # keyword-фильтр, отбор по свежести и по лимиту
│       ├── dedup.py           # SQLiteStore
│       ├── logging_setup.py   # stdout + RotatingFileHandler
│       ├── health.py          # heartbeat-файл и HTTP-эндпоинт /healthz
│       ├── ca_bundle.py       # CA-bundle: certifi + Russian Trusted Root CA
│       ├── certs/
│       │   └── russian_trusted_root_ca.pem
│       ├── collectors/
│       │   ├── base.py        # Protocol Collector
│       │   └── rss.py         # RSSCollector
│       ├── summarizer/
│       │   ├── base.py        # Protocol Summarizer
│       │   ├── gigachat.py    # GigaChatSummarizer, GigaChatClient, ChatClient
│       │   └── prompts.py     # SYSTEM_PROMPT
│       └── publisher/
│           ├── base.py        # Protocol Publisher
│           └── telegram.py    # TelegramPublisher, MessageSender
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── README.md            # что за снимки, когда сняты, какой формат
    │   ├── hnr_feed.xml         # The Hacker News — RSS 2.0, 50 записей
    │   ├── bleepingcomputer_feed.xml  # BleepingComputer — RSS 2.0, 15 записей
    │   ├── php_net_feed.xml     # php.net — Atom 1.0, 5 записей
    │   └── node_feed.xml        # Node.js security advisories — RSS 2.0, 78 записей
    ├── test_filters.py
    ├── test_collectors.py
    ├── test_dedup.py
    ├── test_summarizer.py
    ├── test_gigachat_client.py
    ├── test_publisher.py
    ├── test_pipeline.py
    ├── test_ca_bundle.py
    ├── test_config.py
    └── test_main.py
```

---

## 5. Конвенции

### 5.1. Модели данных (`models.py`)

```python
@dataclass(frozen=True, slots=True)
class NewsItem:
    source: str          # "thehackernews", "php.net", ...
    title: str
    link: str            # канонический URL — используется как ключ дедупликации
    summary: str = ""
    published_at: datetime | None = None

@dataclass(frozen=True, slots=True)
class Digest:
    text: str            # markdown-реферат от GigaChat
    item_count: int
    sources: tuple[str, ...] = ()
    tokens: int = 0      # суммарный usage по всем батчам, если SDK его вернул
```

- Модели **иммутабельны** (`frozen=True`).
- Никаких `dict`-ов между модулями — только dataclass.
- Служебные модели: `FilterResult(matched, rejected)` — результат фильтра;
  `CycleResult(collected, matched, new_items, selected, published, tokens, skipped_reason, items)` —
  итог цикла (`items` заполняется только в `--dry-run`).

### 5.2. Контракты модулей (Protocol)

Каждый этап реализует минимальный `Protocol` из `typing`:

```python
class Collector(Protocol):
    async def collect(self) -> list[NewsItem]: ...

class Summarizer(Protocol):
    async def summarize(self, items: list[NewsItem]) -> Digest: ...

class Publisher(Protocol):
    async def publish(self, digest: Digest) -> None: ...
```

**Правило:** модуль не знает о существовании соседних. Связывает их `pipeline.py`.

Внутренние контракты `MessageSender` (`publisher/telegram.py`) и `ChatClient`
(`summarizer/gigachat.py`) позволяют подменить aiogram и SDK GigaChat моком в тестах.

### 5.3. Конфиг

- Все настройки — через `Settings(BaseSettings)` в `config.py`, читаются из env.
- `.env.example` всегда актуален. **Секреты в репозиторий не коммитим.**
- Обязательные переменные: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `GIGACHAT_CREDENTIALS`.
- Необязательные с дефолтами:
  - расписание и порог: `SCHEDULE_HOURS=4`, `MIN_NEWS_THRESHOLD=2`;
  - ограничения объёма: `MAX_ITEMS_PER_FEED=50`, `MAX_ITEM_AGE_DAYS=3`, `MAX_ITEMS_PER_DIGEST=30` (`0` — без ограничения);
  - GigaChat: `GIGACHAT_MODEL=GigaChat-2-Max`, `GIGACHAT_VERIFY_SSL=true`, `CA_BUNDLE_PATH=./data/ca-bundle.pem`;
  - состояние: `DB_PATH=./data/seen.sqlite`, `DRY_RUN_DB_PATH=./data/seen.dry-run.sqlite`;
  - наблюдаемость: `LOG_LEVEL=INFO`, `LOG_DIR=./data/logs`, `HEALTH_PORT=8080`, `HEARTBEAT_MAX_AGE_SECONDS` (по умолчанию `SCHEDULE_HOURS` + 15 минут);
  - источники: `FEEDS`, `KEYWORDS` (JSON-массивы).
- Формы множественного числа и цифра после ключевого слова допустимы: `vulnerability` матчит `vulnerabilities`, `php` — `php8`.

### 5.4. Логирование

- Стандартный `logging`, JSON-формат не требуется (личный проект). stdout + файл с ротацией.
- На каждую итерацию пайплайна — `cycle_start`, затем `cycle_collected collected=N matched=N new=N selected=N`,
  и итоговая строка: `cycle_skipped reason=threshold ...` или `cycle_done items=5 sent=True tokens=1234 marked=5`.
- Все сетевые ошибки — `WARNING` с URL, но без тела ответа (чтобы случайно не утащить токены/секреты в логи).
- Никогда не логировать содержимое `.env`, ключи GigaChat, токен Telegram.
- Исключения SDK `gigachat` не логировать как есть: их сообщение содержит тело и заголовки ответа.
  `GigaChatClient` превращает их в `SummarizerError` (тип ошибки + статус, без тела).
- Ошибка конфигурации — одна строка `configuration_error ...` без значений переменных; код выхода 2.

### 5.5. Обработка ошибок

- Падение **одного** RSS-фида не должно ломать цикл: `try/except` вокруг `collect()`, лог + пустой список.
- Ошибка GigaChat или Telegram — лог + **не** отмечать новости как «обработанные» в SQLite (чтобы они попали в следующий цикл).
- Пустой ответ модели — ошибка (`EmptyResponseError`), а не «пустой дайджест».
- Retry — только для временных сбоев (сетевые ошибки, 429, 5xx — `RETRYABLE_STATUS_CODES`); например, 401 не повторяется.
- Все внешние вызовы — с таймаутом (`httpx`/`feedparser`).
- При SIGTERM и Ctrl-C вызывается `scheduler.shutdown(wait=True)`: идущий цикл доделывает работу в пределах `stop_grace_period`.

### 5.6. Стиль кода

- `ruff format`, длина строки 100.
- `mypy --strict` обязателен для `src/` и для `tests/`; для библиотек без `py.typed` (`feedparser`, `apscheduler`) — `ignore_missing_imports` в `pyproject.toml`.
- Импорты: `from __future__ import annotations` в каждом модуле.
- Async там, где есть I/O. Синхронные блоки — только если вызваны из `asyncio.to_thread`.

### 5.7. Тесты

- **Не делаем реальных сетевых вызовов.** Все внешние API — через `respx` (httpx) или моки.
- RSS-фикстуры лежат в `tests/fixtures/` — это **реальные снимки лент** (сняты с
  серверов 2026-11-26, переводы строк приведены к LF). Состав и формат — в
  `tests/fixtures/README.md`. Тесты на них точные: при обновлении снимка
  ожидаемые заголовки, даты и счётчики правятся осознанно.
- Тесты не читают `.env`: значения по умолчанию проверяются через подкласс `Settings` с `env_file=None`.
- Минимальный набор на каждый модуль:
  - `test_filters.py`: ключевые слова матчатся/не матчатся, регистр, несколько слов; отбор по свежести и лимиту.
  - `test_collectors.py`: разбор реальных снимков (RSS 2.0 и Atom), лента без описаний, канонизация ссылок, падение одного фида.
  - `test_dedup.py`: повторная вставка того же `link` возвращает `False`; после `mark_sent` — не попадает в выдачу.
  - `test_summarizer.py`: ответ замокан, проверяем, что промпт содержит все заголовки; батчи, retry.
  - `test_gigachat_client.py`: адаптер поверх настоящего SDK, сеть замокана `respx`.
  - `test_publisher.py`: разметка MarkdownV2, разбивка по 4096.
  - `test_pipeline.py`: сценарий «мало новостей → publish не вызывается» и «достаточно → publish вызван один раз»; сбой не помечает новости.
  - `test_ca_bundle.py`, `test_config.py`, `test_main.py`: TLS-доверие, конфиг, CLI, health-эндпоинт, остановка планировщика.

---

## 6. Команды

Все команды выполняются **внутри Docker**. На хосте нужны только `docker` и `bash`.

```bash
bash dbot.sh help            # список команд

bash dbot.sh build           # собрать образы (runtime + dev)
bash dbot.sh up              # запустить сервис в фоне
bash dbot.sh logs            # логи сервиса
bash dbot.sh down            # остановить сервис

bash dbot.sh dry-run         # один цикл без GigaChat и Telegram — секреты не нужны
bash dbot.sh run-once        # один рабочий цикл: GigaChat + Telegram

bash dbot.sh ci              # ruff check + ruff format --check + mypy + pytest
bash dbot.sh test            # pytest; аргументы пробрасываются: bash dbot.sh test -k filters
bash dbot.sh lint            # ruff check
bash dbot.sh format          # ruff format (изменить файлы)
bash dbot.sh mypy            # mypy --strict
bash dbot.sh shell           # оболочка в dev-контейнере
bash dbot.sh backup          # бэкап SQLite (сервис должен работать)

bash dbot.sh python -V       # любая другая команда dev-контейнера
```

- `dbot.sh` — обёртка над `docker compose`; `COMPOSE="docker compose -f …"` позволяет подставить свой файл.
- `Makefile` — опциональная обёртка над `dbot.sh` для тех, у кого есть `make`.
- Сервисы compose: `bot` (планировщик), `once` и `dry-run` (профиль `oneshot`), `tools` (профиль `tools`).

`AGENTS.md` должен поддерживаться в актуальном состоянии: **если меняется контракт модуля или структура — обнови раздел.**

---

## 7. Ключевые проектные решения (не менять без явной причины)

1. **SQLite как источник правды для дедупликации.** Не переезжаем на Redis/Postgres, пока не появится объективная причина (несколько инстансов, >100k записей).
2. **Порог проверяется ДО обращения к GigaChat.** Это экономит токены Freemium.
3. **GigaChat-промпт — в отдельном файле** `src/digest_bot/summarizer/prompts.py`, а не зашит в код. Легко тюнить.
4. **Никаких зависимостей «на всякий случай».** Каждый пакет в `pyproject.toml` должен быть использован.
5. **Планировщик внутри процесса.** Не переезжаем на внешний cron, пока процесс стабилен.
6. **Новости не теряются при сбое.** Ссылки помечаются в SQLite только после успешной публикации; сбой реферата или публикации оставляет новости следующему циклу.
7. **Ограничение объёма — часть контракта.** Без лимита записей на фид и лимита новостей на дайджест один фид-архив превращался в десятки вызовов GigaChat за цикл.
8. **TLS к GigaChat проверяется, а не отключается.** OAuth-сервер GigaChat работает на сертификате Минцифры; корень встроен в пакет, его отпечаток закреплён в `ca_bundle.py` и проверяется при использовании. `GIGACHAT_VERIFY_SSL=false` — аварийный выход, а не норма.
9. **Фиды по умолчанию — только существующие.** У nginx.org нет RSS, новости о nginx приходят из агрегаторов по ключевому слову; для Node.js берётся фид security-advisories.
10. **Healthcheck отражает ход циклов, а не только живость процесса.** `/healthz` отдаёт 503, если heartbeat протух или 3 цикла подряд упали.
11. **Первый цикл — сразу после старта**, а не через `SCHEDULE_HOURS`.
12. **Сигналы обрабатываются явно.** В compose включён `init: true`, обработчик SIGTERM ставится в начале `main()` — иначе сигнал, пришедший при старте, теряется.

---

## 8. Чего не делать

- ❌ Не запускать инструменты (pytest, ruff, mypy) на хосте — только через `bash dbot.sh` или `docker compose`.
- ❌ Не хардкодить URL, ключи, ID каналов — только через `Settings`.
- ❌ Не вызывать `feedparser.parse(url)` синхронно в async-коде без `to_thread`.
- ❌ Не отправлять в Telegram сообщения длиннее 4096 символов — разбивать на части в `TelegramPublisher` (лимит считается в UTF-16 кодовых единицах: эмодзи занимает две).
- ❌ Не логировать `response.text` от GigaChat целиком (может содержать длинный пользовательский контент).
- ❌ Не логировать исключения SDK `gigachat` как есть — в их сообщении тело и заголовки ответа.
- ❌ Не отключать проверку TLS к GigaChat ради «чтобы заработало» — нужный корень уже встроен.
- ❌ Не помечать новости в SQLite до успешной публикации.
- ❌ Не добавлять фид, не проверив, что он существует и отдаёт разумное число записей.
- ❌ Не коммитить `data/*.sqlite`, `.env`, `__pycache__`.
- ❌ Не использовать `print()` — только `logging`.
- ❌ Не добавлять retry без backoff (использовать `tenacity` с `wait_exponential`).

---

## 9. Definition of Done (для любой задачи)

- [ ] Код прошёл `ruff check` и `mypy --strict`.
- [ ] На новую логику есть тест(ы).
- [ ] `pytest -q` зелёный.
- [ ] Все проверки выполнены внутри Docker: `bash dbot.sh ci`.
- [ ] Если изменён контракт модуля — обновлён `AGENTS.md` и `README.md`.
- [ ] Если добавлена env-переменная — обновлён `.env.example`.
- [ ] Ручной прогон `bash dbot.sh run-once` проходит без ошибок (при доступных внешних API).

---

# План разработки

План разбит на фазы так, чтобы **каждая фаза заканчивалась работающим инкрементом**. После каждой фазы есть «демо-точка», на которой можно запустить сервис и увидеть эффект.

> **Статус.** Фазы 0–6 реализованы при создании корпуса проекта: `[x]` — сделано.
> Что именно проверено, а что осталось непроверенным, — в конце плана.

## Фаза 0 — Bootstrap (0.5–1 день)

**Цель:** пустой, но запускаемый скелет проекта.

- [x] Создать структуру каталогов из `AGENTS.md`.
- [x] `pyproject.toml` с зависимостями и `[project.optional-dependencies].dev`.
- [x] `config.py` на `pydantic-settings` + `.env.example`.
- [x] `models.py` (`NewsItem`, `Digest`).
- [x] `__main__.py` с CLI на `argparse`: `run` и `run --once`.
- [x] `ruff`, `mypy`, `pytest` настроены и проходят на пустом коде.
- [x] `Dockerfile` (multi-stage, Python 3.11-slim, non-root user).
- [x] `.gitignore`, `README.md` (краткий), `AGENTS.md`.
- [x] CI (GitHub Actions): lint + mypy + tests. Файл добавлен, на GitHub не запускался.

**Демо:** `bash dbot.sh dry-run` печатает собранные новости и завершается с кодом 0.

---

## Фаза 1 — Сбор и фильтрация (1–2 дня)

**Цель:** собрать новости из RSS и отфильтровать по стеку.

- [x] `collectors/rss.py`: `RSSCollector` с `httpx.AsyncClient` (User-Agent `SecurityFeedBot/1.0`) и `feedparser`.
- [x] Список фидов — в `config.py`. По умолчанию: The Hacker News, BleepingComputer, PHP.net, Node.js security advisories. Отклонение от плана: у nginx.org нет RSS, а общий блог Node.js отдаёт архив на 1000+ записей (решение 9).
- [x] `filters.py`: keyword-фильтр по `title + summary` (нижний регистр, `KEYWORDS` в конфиге, включая `php`, `linux`, `nginx`, `node.js`, `npm`, `cve`, `rce`, `xss`, `vulnerability`).
- [x] Модель `NewsItem` заполняется корректно (`published_at` парсится, если есть).
- [x] Тесты: `test_filters.py` + `test_collectors.py` на фикстурах.
- [x] Ошибка одного фида не валит цикл.

**Демо:** `bash dbot.sh dry-run` печатает список отфильтрованных новостей в stdout.

---

## Фаза 2 — Дедупликация (1 день)

**Цель:** одна и та же новость не обрабатывается дважды.

- [x] `dedup.py`: `SQLiteStore` с методами `filter_new(items) -> list[NewsItem]`, `mark_sent(items)`.
- [x] Схема: `seen(link TEXT PRIMARY KEY, sent_at TIMESTAMP NOT NULL)`.
- [x] Миграции не нужны (одна таблица), но версия схемы хранится в `PRAGMA user_version`.
- [x] Тесты: повторная вставка, `mark_sent` идемпотентен, `filter_new` возвращает только новые.
- [x] Каталог `data/` создаётся автоматически.

**Демо:** двойной прогон `bash dbot.sh dry-run` — второй раз показывает 0 новых.

---

## Фаза 3 — Публикация в Telegram (без GigaChat) (1 день)

**Цель:** увидеть реальные новости в канале.

- [x] `publisher/telegram.py`: `TelegramPublisher` на `aiogram` 3.
- [x] Разбивка сообщения по 4096 символов, `parse_mode=MarkdownV2` с корректным escape.
- [x] Тест на `respx`/мок aiogram: длина > 4096 → 2 сообщения.
- [x] `mark_sent` вызывается **только после** успешной публикации.
- ~~Временный формат: список «Заголовок — ссылка» без суммаризации.~~ Пропущено: фазы 3 и 5 реализованы вместе.

**Демо:** `bash dbot.sh run-once` — в канале появляется дайджест.

---

## Фаза 4 — Порог и планировщик (0.5 дня)

**Цель:** цикл раз в 4 часа с пропуском при малом количестве новостей.

- [x] В `pipeline.py` — проверка `if len(selected) < MIN_NEWS_THRESHOLD: return`.
- [x] `main.py`: `BlockingScheduler` с `IntervalTrigger(hours=settings.SCHEDULE_HOURS)`.
- [x] Метрика в логе: `cycle_done items=3 sent=True tokens=N`.
- [x] `test_pipeline.py`: сценарии «мало → publish не вызван», «достаточно → вызван».
- [x] Heartbeat-строка в логе на старте (`scheduler_started interval_hours=4`).

**Демо:** сервис крутится сутки в Docker на VPS, в логах видно пропуски.

---

## Фаза 5 — GigaChat-реферирование (1–2 дня)

**Цель:** вместо списка — связный реферат.

- [x] `summarizer/gigachat.py`: `GigaChatSummarizer` на официальном SDK.
- [x] `summarizer/prompts.py`: `SYSTEM_PROMPT` для реферирования (адаптированный).
- [x] Передача всех `NewsItem` одним вызовом; при >30 новостях — батчи по 30.
- [x] Обработка ошибок API: retry с `tenacity`, лог `gigachat_retry`.
- [x] Тест: `respx`-мок ответа GigaChat, проверка промпта и разбора ответа.
- [x] Метрика `tokens` в логе (если SDK возвращает usage).
- [x] Freemium: конфиг `GIGACHAT_MODEL=GigaChat-2-Max` по умолчанию.

**Демо:** в канале появляется структурированный markdown-реферат вместо списка.

---

## Фаза 6 — Эксплуатация (0.5–1 день)

**Цель:** стабильность и наблюдаемость.

- [x] Docker-compose с volume для `data/`.
- [x] Health-check: HTTP-эндпоинт `/healthz` на `http.server` **и** heartbeat-файл.
- [x] Ротация логов (`logging.handlers.RotatingFileHandler`).
- [x] Скрипт `scripts/backup-db.sh` (копия SQLite локально).
- [x] `README.md`: как развернуть, как настроить секреты, как мониторить.
- [x] Проверка лимитов Telegram (30 msg/sec в канал) — пауза `asyncio.sleep(1)` между сообщениями.

**Демо:** сервис работает неделю без ручного вмешательства.

---

## Фаза 7 (опционально) — Улучшения

Идеи, которые НЕ входят в MVP, но логично добавить позже:

- **CISA KEV enrichment:** проверять CVE из новостей по публичному KEV-фиду и помечать «⚠️ эксплуатируется».
- **Приоритизация источников:** официальные advisories (PHP.net, Node.js) идут первыми в реферате.
- **История в `/digest` боте-команде:** отдельный бот, который отдаёт последние N дайджестов по запросу.
- **Экспорт в отдельный канал по темам:** `php`, `linux`, `node` — отдельные каналы.
- **GigaChat-Lite как fallback:** при ошибке Max-модели — переключение на Lite.
- **Алерт владельцу в личку** (отдельный chat_id), если API GigaChat стабильно падает 2+ цикла подряд.

---

## Оценка сроков (для одного разработчика в свободном режиме)

| Фаза | Оценка |
| :--- | :--- |
| 0. Bootstrap | 0.5–1 день |
| 1. Сбор + фильтр | 1–2 дня |
| 2. Дедупликация | 1 день |
| 3. Telegram | 1 день |
| 4. Порог + scheduler | 0.5 дня |
| 5. GigaChat | 1–2 дня |
| 6. Эксплуатация | 0.5–1 день |
| **Итого MVP** | **~6–9 дней** |

Фазы 1–4 можно распараллелить, если работаете вдвоём.

---

## Что проверено, а что нет

**Проверено в контейнере (`security-digest-bot:runtime`):**

- `bash dbot.sh ci`: `ruff check`, `ruff format --check`, `mypy --strict`, все тесты.
- Сбор RSS с реальных лент, фильтр, дедуп, отбор по свежести и лимиту (`dry-run`).
- Планировщик, первый цикл сразу после старта, `/healthz`, статус `healthy`.
- TLS к OAuth-серверу GigaChat с включённой проверкой сертификата (с заглушечным ключом сервер отвечает по HTTP).
- Остановка по SIGTERM: на покое и во время идущего цикла — код 0; при остановке в момент старта — без зависания.
- Сервис работает в контейнере от пользователя `appuser`.

**Не проверено — нужны секреты или целевая среда:**

- Настоящий реферат GigaChat и настоящая публикация в Telegram: нужны `GIGACHAT_CREDENTIALS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`. Проверить после заполнения `.env`: `bash dbot.sh run-once`.
- CI на GitHub (workflow не запускался — локально проходит `bash dbot.sh ci`).
- Демо-точки «сутки» и «неделя на VPS».
