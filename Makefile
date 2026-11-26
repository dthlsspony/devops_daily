# Обёртка для тех, у кого есть make. Вся логика — в dbot.sh: команды идентичны,
# например `make test` и `bash dbot.sh test`.
.RECIPEPREFIX = >
S := bash dbot.sh

.DEFAULT_GOAL := help
.PHONY: help build up down restart status logs run-once dry-run lint format format-check mypy test ci shell backup clean

help: ## Показать список команд
>@$(S) help

build: ## Собрать образы (runtime + dev)
>$(S) build

up: ## Запустить сервис в фоне (планировщик + healthcheck)
>$(S) up

down: ## Остановить сервис
>$(S) down

restart: ## Пересобрать и перезапустить сервис
>$(S) restart

status: ## Статус контейнеров
>$(S) status

logs: ## Логи сервиса (follow)
>$(S) logs

run-once: ## Один рабочий цикл: GigaChat + Telegram
>$(S) run-once

dry-run: ## Один цикл без GigaChat и Telegram (печать в stdout)
>$(S) dry-run

lint: ## ruff check
>$(S) lint

format: ## ruff format (изменить файлы)
>$(S) format

format-check: ## ruff format --check
>$(S) format-check

mypy: ## mypy --strict по src и tests
>$(S) mypy

test: ## pytest
>$(S) test

ci: ## Полная проверка: lint + format-check + mypy + pytest
>$(S) ci

shell: ## Shell внутри dev-контейнера
>$(S) shell

backup: ## Бэкап SQLite-базы в ./data/backups (сервис должен быть запущен)
>$(S) backup

clean: ## Удалить локальные кэши инструментов
>$(S) clean
