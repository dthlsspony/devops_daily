# security-digest-bot

Дайджест новостей по информационной безопасности: RSS-ленты → фильтр по ключевым
словам → дедупликация в SQLite → отбор свежих новостей → реферат от GigaChat →
публикация в Telegram-канал.

Личный, некоммерческий проект. GigaChat используется в режиме Freemium.

**Всё выполняется в Docker.** Локальный Python не нужен: на хосте нужны только
`docker` и `bash`. Единственная команда — `bash dbot.sh <команда>`.

---

## 1. Как это работает

```
[APScheduler: каждые SCHEDULE_HOURS, первый цикл — сразу после старта]
        │
        ▼
[RSSCollector] ──► [keyword-фильтр] ──► [SQLite: дедуп по link]
                                                │
                                                ▼
                                   [отбор: свежесть + лимит за цикл]
                                                │
                                                ▼
                                   [порог MIN_NEWS_THRESHOLD]
                                   (мало новостей → цикл пропускается)
                                                │
                                                ▼
                                   [GigaChat: реферат]
                                                │
                                                ▼
                                   [TelegramPublisher: MarkdownV2]
```

Что важно знать о поведении:

- **Порог проверяется до обращения к GigaChat** — экономит токены Freemium.
- **Объём ограничен**: из фида берётся не более `MAX_ITEMS_PER_FEED` записей, в
  дайджест попадают новости не старше `MAX_ITEM_AGE_DAYS` дней, и не больше
  `MAX_ITEMS_PER_DIGEST` за цикл. Остальные дождутся следующего цикла. Без этого
  один фид-архив превращался в десятки вызовов GigaChat за цикл.
- **Новости не теряются при сбое**: ссылка попадает в SQLite только после успешной
  публикации. Сбой GigaChat или Telegram — новости уйдут в следующем цикле.
- Падение одной RSS-ленты не ломает цикл.
- Длинные дайджесты режутся на сообщения по 4096 символов.
- **TLS к GigaChat проверяется.** OAuth-сервер GigaChat работает на сертификате
  Минцифры; нужный корневой сертификат встроен в пакет (`src/digest_bot/certs/`),
  ничего устанавливать не нужно.

---

## 2. Быстрый старт

```bash
cp .env.example .env      # заполнить токен Telegram, канал и ключ GigaChat
bash dbot.sh up           # собрать и запустить сервис в фоне
bash dbot.sh logs         # смотреть логи (Ctrl-C — выйти из просмотра)
```

Проверить, что всё живо:

```bash
curl http://127.0.0.1:8080/healthz
```

Пробный прогон без публикации — собирает новости и печатает, что уйдёт в дайджест.
Секреты не нужны:

```bash
bash dbot.sh dry-run
```

Разовый рабочий цикл (GigaChat + Telegram), без планировщика:

```bash
bash dbot.sh run-once
```

> Порт `8080` на хосте можно сменить переменной `HEALTH_PORT` в `.env`.

---

## 3. Команды

`dbot.sh` — обёртка над `docker compose`. Если удобнее `make` — `Makefile` делает
то же самое (`make test` = `bash dbot.sh test`).

| Команда | Что делает |
| :--- | :--- |
| `bash dbot.sh build` | собрать образы (runtime + dev) |
| `bash dbot.sh up` | собрать и запустить сервис в фоне |
| `bash dbot.sh down` | остановить сервис |
| `bash dbot.sh restart` | пересобрать и перезапустить |
| `bash dbot.sh status` | статус контейнеров |
| `bash dbot.sh logs` | логи сервиса |
| `bash dbot.sh dry-run` | цикл без GigaChat и Telegram (секреты не нужны) |
| `bash dbot.sh run-once` | один рабочий цикл |
| `bash dbot.sh backup` | бэкап SQLite-базы в `data/backups` (сервис должен работать) |
| `bash dbot.sh ci` | `ruff check` + `ruff format --check` + `mypy --strict` + `pytest` |
| `bash dbot.sh test` | тесты; аргументы пробрасываются: `bash dbot.sh test -k filters` |
| `bash dbot.sh lint` | `ruff check` |
| `bash dbot.sh format` | `ruff format` (изменить файлы) |
| `bash dbot.sh format-check` | проверка форматирования |
| `bash dbot.sh mypy` | `mypy --strict` |
| `bash dbot.sh shell` | оболочка в dev-контейнере |
| `bash dbot.sh python -V` | любая другая команда dev-контейнера |

Тесты, линтер и типизация никогда не запускаются на хосте — только в контейнере
`tools`. Кэши инструментов остаются в контейнере и не появляются в каталоге проекта.

Нужен другой compose-файл: `COMPOSE="docker compose -f docker-compose.yml -f prod.yml" bash dbot.sh up`.

---

## 4. Переменные окружения

Полный список с комментариями — в [`.env.example`](.env.example).

| Переменная | Обязательна | По умолчанию | Назначение |
| :--- | :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | да | — | токен бота от @BotFather |
| `TELEGRAM_CHANNEL_ID` | да | — | ID канала/чата |
| `GIGACHAT_CREDENTIALS` | да | — | base64-ключ GigaChat |
| `GIGACHAT_MODEL` | нет | `GigaChat-2-Max` | модель GigaChat |
| `GIGACHAT_VERIFY_SSL` | нет | `true` | проверка TLS к GigaChat; не отключайте без необходимости |
| `MIN_NEWS_THRESHOLD` | нет | `2` | минимум отобранных новостей за цикл |
| `SCHEDULE_HOURS` | нет | `4` | период запуска цикла, часов |
| `MAX_ITEMS_PER_FEED` | нет | `50` | сколько последних записей брать из фида |
| `MAX_ITEM_AGE_DAYS` | нет | `3` | новости старше N дней в дайджест не попадают; `0` — без ограничения |
| `MAX_ITEMS_PER_DIGEST` | нет | `30` | максимум новостей в одном дайджесте; `0` — без ограничения |
| `DB_PATH` | нет | `./data/seen.sqlite` | база дедупликации |
| `DRY_RUN_DB_PATH` | нет | `./data/seen.dry-run.sqlite` | отдельная база для `dry-run` |
| `CA_BUNDLE_PATH` | нет | `./data/ca-bundle.pem` | куда собирается CA-bundle для GigaChat |
| `REQUEST_TIMEOUT` | нет | `20` | таймаут сетевых запросов, сек |
| `TELEGRAM_RATE_LIMIT_DELAY` | нет | `1.0` | пауза между частями длинного сообщения |
| `LOG_LEVEL` / `LOG_DIR` | нет | `INFO` / `./data/logs` | логи: stdout + файл с ротацией |
| `HEALTH_PORT` | нет | `8080` | порт на хосте для `/healthz` |
| `HEARTBEAT_MAX_AGE_SECONDS` | нет | `SCHEDULE_HOURS` + 15 мин | когда heartbeat считается протухшим |
| `FEEDS` / `KEYWORDS` | нет | см. `config.py` | JSON-массивы для переопределения фидов и ключевых слов |

Секреты в репозиторий не коммитятся: `.env` в `.gitignore`.

### Источники по умолчанию

The Hacker News, BleepingComputer, php.net (релизы), Node.js (security advisories).

Два отличия от того, что можно было ожидать:

- **nginx.org не публикует RSS.** Новости о nginx попадают в дайджест из
  агрегаторов по ключевому слову `nginx`. Свою ленту можно добавить через `FEEDS`.
- Для Node.js берётся фид security-advisories, а не общий блог: общий блог отдаёт
  архив на 1000+ записей, где совпадает каждый заголовок.

---

## 5. Структура проекта

Полное дерево — в [AGENTS.md](AGENTS.md), раздел 4.

```
.
├── AGENTS.md                  # контракты, конвенции, команды, статус плана
├── dbot.sh                    # все команды через Docker
├── docker-compose.yml         # bot, once, dry-run, tools
├── docker/
│   ├── Dockerfile             # multi-stage: base / dev / runtime
│   └── entrypoint.sh          # каталог данных → appuser, затем сброс root
├── scripts/backup-db.sh       # бэкап SQLite через backup API
├── pyproject.toml
├── src/digest_bot/
│   ├── main.py                # CLI, планировщик, SIGTERM
│   ├── pipeline.py            # оркестратор этапов
│   ├── config.py, models.py, filters.py, dedup.py
│   ├── health.py, logging_setup.py, ca_bundle.py
│   ├── collectors/            # base.py (Protocol), rss.py
│   ├── summarizer/            # base.py, gigachat.py, prompts.py
│   └── publisher/             # base.py, telegram.py
└── tests/                     # pytest + respx, без реальных сетевых вызовов
    └── fixtures/              # реальные снимки лент + README о них
```

---

## 6. Разработка

```bash
bash dbot.sh ci        # полный прогон: ruff check, ruff format --check, mypy, pytest
bash dbot.sh test      # только тесты
bash dbot.sh format    # отформатировать код
```

Тесты не ходят в сеть: RSS отдаются реальными снимками лент из `tests/fixtures/`
(состав — в `tests/fixtures/README.md`), HTTP-моки сделаны через `respx`
(включая адаптер над настоящим SDK GigaChat). Тесты не читают `.env`, поэтому
локальные секреты на них не влияют.

CI (GitHub Actions) запускает те же проверки в Docker: `.github/workflows/ci.yml`.

---

## 7. Эксплуатация

**Healthcheck.** `GET /healthz` отдаёт `200`, если сервис здоров, иначе `503`:

```json
{"status": "ok", "cycle": "ok", "age_seconds": 4180, "details": {"items": 5, "sent": true}}
```

Сервис считается нездоровым, если последний цикл был слишком давно (по умолчанию
`SCHEDULE_HOURS` + 15 минут) или три цикла подряд упали. Один сбой — например,
кратковременная недоступность API — тревогой не считается. Docker помечает
контейнер `healthy`/`unhealthy` по этому же эндпоинту.

**Логи.** В stdout и в `data/logs/digest_bot.log` с ротацией (5 МБ × 5 файлов).
Секреты и тела ответов GigaChat в лог не пишутся. Ключевые строки: `cycle_collected`,
`cycle_skipped`, `cycle_done items=N sent=True tokens=M`, `cycle_failed`.

**Остановка.** `bash dbot.sh down` (или `docker compose stop`) даёт идущему циклу
доделать работу — до `stop_grace_period` (120 секунд).

**Бэкап.** `bash dbot.sh backup` копирует базу через SQLite backup API (корректно,
даже пока бот работает) в `data/backups/`.

**Права на каталог данных.** На Linux Docker создаёт `./data` от root. Контейнер
стартует от root только для того, чтобы `entrypoint.sh` отдал этот каталог
пользователю `appuser`, после чего сервис работает от `appuser`. В образе по
умолчанию уже задан непривилегированный пользователь, поэтому `docker run` без
compose тоже безопасен.

---

## 8. Что проверить после заполнения `.env`

Публикация в настоящий Telegram-канал и реферат от настоящей модели при создании
корпуса не проверялись — для них нужны ваши ключи. После заполнения `.env`:

```bash
bash dbot.sh run-once      # один полный цикл: реферат должен появиться в канале
bash dbot.sh up            # затем — постоянная работа по расписанию
```

Если в канале пусто, смотрите `bash dbot.sh logs`: там видно, на каком этапе цикл
остановился (`cycle_skipped reason=threshold` означает, что новых новостей меньше
порога — это не ошибка).
