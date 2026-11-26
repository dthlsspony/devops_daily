#!/usr/bin/env bash
# Единая точка входа для всех команд проекта. Всё выполняется внутри Docker:
# на хосте нужны только docker и bash.
#
#   bash dbot.sh help
#
# Дополнительные аргументы передаются команде как есть:
#   bash dbot.sh test -k filters
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# COMPOSE можно переопределить, например: COMPOSE="docker compose -f prod.yml"
read -r -a compose <<< "${COMPOSE:-docker compose}"

usage() {
    cat <<'EOF'
Использование: bash dbot.sh <команда>

Сервис:
  build          собрать образы (runtime + dev)
  up             запустить сервис в фоне (планировщик + healthcheck)
  down           остановить сервис
  restart        пересобрать и перезапустить сервис
  status         статус контейнеров
  logs           логи сервиса (follow)
  run-once       один рабочий цикл: GigaChat + Telegram
  dry-run        один цикл без GigaChat и Telegram (печать в stdout)
  backup         бэкап SQLite-базы в data/backups (сервис должен работать)

Инструменты (внутри dev-контейнера):
  lint           ruff check
  format         ruff format (изменить файлы)
  format-check   ruff format --check
  mypy           mypy --strict
  test           pytest
  ci             lint + format-check + mypy + pytest
  shell          интерактивная оболочка в dev-контейнере
  <инструмент>   любая другая команда dev-контейнера, например: python -V

Прочее:
  clean          удалить локальные кэши инструментов
  help           это сообщение
EOF
}

command_name="${1:-help}"
if [ $# -gt 0 ]; then
    shift
fi

tools() {
    "${compose[@]}" --profile tools run --rm tools "$@"
}

case "${command_name}" in
    build)
        "${compose[@]}" build bot
        "${compose[@]}" --profile tools build tools
        ;;
    up | restart)
        "${compose[@]}" up -d --build bot
        ;;
    down) "${compose[@]}" down ;;
    status) "${compose[@]}" ps -a ;;
    logs) "${compose[@]}" logs -f bot ;;
    run-once)
        "${compose[@]}" build bot
        "${compose[@]}" --profile oneshot run --rm once
        ;;
    dry-run)
        "${compose[@]}" build bot
        "${compose[@]}" --profile oneshot run --rm dry-run
        ;;
    backup) bash scripts/backup-db.sh ;;
    lint) tools ruff check src tests "$@" ;;
    format) tools ruff format src tests "$@" ;;
    format-check) tools ruff format --check src tests "$@" ;;
    mypy) tools mypy src tests "$@" ;;
    test) tools pytest -q "$@" ;;
    ci) tools sh -c 'ruff check src tests && ruff format --check src tests && mypy src tests && pytest -q' ;;
    shell) tools sh ;;
    clean) rm -rf .mypy_cache .pytest_cache .ruff_cache ;;
    help | -h | --help) usage ;;
    *) tools "${command_name}" "$@" ;;
esac
