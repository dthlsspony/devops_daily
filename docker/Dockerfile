# syntax=docker/dockerfile:1
#
# Мульти-стадийный образ:
#   base    — Python и зависимости проекта;
#   dev     — base + инструменты разработки (ruff, mypy, pytest);
#   runtime — только рантайм.
#
# Сборка:  docker build -f docker/Dockerfile --target runtime -t security-digest-bot .

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    TZ=UTC

WORKDIR /app

# Сначала манифест, потом исходники: слой с зависимостями переиспользуется.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .


FROM base AS dev

COPY tests ./tests
RUN pip install --no-cache-dir ".[dev]"


FROM base AS runtime

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Образ безопасен по умолчанию: сервис запускается от appuser. docker compose
# запускает контейнер от root (user: "0:0"), чтобы entrypoint смог отдать
# примонтированный каталог данных пользователю appuser и сбросить привилегии.
USER appuser

ENV HOME=/home/appuser \
    DB_PATH=/app/data/seen.sqlite \
    DRY_RUN_DB_PATH=/app/data/seen.dry-run.sqlite \
    HEARTBEAT_PATH=/app/data/heartbeat.json \
    LOG_DIR=/app/data/logs \
    HEALTH_HOST=0.0.0.0 \
    HEALTH_PORT=8080

VOLUME ["/app/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["entrypoint.sh"]
CMD ["python", "-m", "digest_bot", "run"]
