#!/bin/sh
# Точка входа контейнера: подготавливает каталог данных и запускает сервис.
#
# Если контейнер запущен от root (так делает docker compose), примонтированный
# каталог /app/data передаётся пользователю appuser и привилегии сбрасываются.
# Иначе процесс уже непривилегированный — просто запускаем команду.
set -eu

DATA_DIR=/app/data

if [ "$(id -u)" = "0" ]; then
    mkdir -p "${DATA_DIR}"
    if ! chown -R appuser:appuser "${DATA_DIR}" 2>/dev/null; then
        echo "entrypoint: cannot chown ${DATA_DIR}; continuing as appuser" >&2
    fi
    exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

exec "$@"
