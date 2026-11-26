#!/usr/bin/env bash
# Резервная копия SQLite-базы дедупликации в data/backups (сервис должен работать).
# Используется backup API SQLite, поэтому копия корректна даже при работающем боте.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# COMPOSE можно переопределить, например: COMPOSE="docker compose -f prod.yml"
read -r -a compose <<< "${COMPOSE:-docker compose}"

"${compose[@]}" exec -T --user appuser bot python - <<'PY'
import datetime
import pathlib
import sqlite3

source = pathlib.Path("/app/data/seen.sqlite")
backup_dir = pathlib.Path("/app/data/backups")
backup_dir.mkdir(parents=True, exist_ok=True)
target = backup_dir / f"seen-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d-%H%M%S}.sqlite"

with sqlite3.connect(source) as origin, sqlite3.connect(target) as destination:
    origin.backup(destination)

print(f"backup_created {target}")
PY
