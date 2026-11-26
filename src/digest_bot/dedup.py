"""Дедупликация новостей на основе SQLite.

Хранилище — источник правды: ссылка попадает в таблицу ``seen`` только после
успешной публикации. Если сборка реферата или отправка в Telegram сорвалась,
ссылка не помечается и новость попадёт в следующий цикл.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from digest_bot.models import NewsItem

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_CHUNK_SIZE = 400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    link TEXT PRIMARY KEY,
    sent_at TIMESTAMP NOT NULL
);
"""


class SQLiteStore:
    """Множество ссылок, которые уже были опубликованы."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> SQLiteStore:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def db_path(self) -> Path:
        """Путь к файлу базы."""
        return self._db_path

    @property
    def connection(self) -> sqlite3.Connection:
        """Открытое соединение (иначе — ошибка программиста)."""
        if self._connection is None:
            raise RuntimeError("SQLiteStore не открыт: вызовите open() или используйте with")
        return self._connection

    def open(self) -> None:
        """Создать файл базы, каталог и таблицу при необходимости."""
        if self._connection is not None:
            return
        parent = self._db_path.parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.executescript(_SCHEMA)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
        self._connection = connection
        logger.info("store_opened path=%s", self._db_path)

    def close(self) -> None:
        """Закрыть соединение."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _seen_links(self, links: Iterable[str]) -> set[str]:
        """Вернуть подмножество ссылок, которые уже есть в хранилище."""
        candidates = list(dict.fromkeys(links))
        if not candidates:
            return set()
        found: set[str] = set()
        for start in range(0, len(candidates), _CHUNK_SIZE):
            chunk = candidates[start : start + _CHUNK_SIZE]
            placeholders = ", ".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"SELECT link FROM seen WHERE link IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update(str(row["link"]) for row in rows)
        return found

    def filter_new(self, items: Sequence[NewsItem]) -> list[NewsItem]:
        """Оставить только те новости, которых ещё не было в хранилище.

        Дубликаты внутри одной выборки также отбрасываются.
        """
        seen = self._seen_links(item.link for item in items)
        new_items: list[NewsItem] = []
        local: set[str] = set()
        for item in items:
            if item.link in seen or item.link in local:
                continue
            local.add(item.link)
            new_items.append(item)
        return new_items

    def mark_seen(self, link: str) -> bool:
        """Пометить ссылку как обработанную.

        Возвращает ``False``, если ссылка уже была в хранилище.
        """
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO seen (link, sent_at) VALUES (?, ?)",
            (link, datetime.now(UTC).isoformat()),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def mark_sent(self, items: Sequence[NewsItem]) -> int:
        """Пометить новости как опубликованные, вернуть число новых."""
        marked = 0
        for item in items:
            if self.mark_seen(item.link):
                marked += 1
        return marked

    def seen_count(self) -> int:
        """Сколько всего ссылок сохранено."""
        row = self.connection.execute("SELECT COUNT(*) AS total FROM seen").fetchone()
        return int(row["total"]) if row else 0
