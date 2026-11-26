"""Immutable data models exchanged between pipeline stages.

Модели — единственный способ передачи данных между модулями: никаких ``dict``-ов
на границах этапов. Все модели иммутабельны (``frozen=True``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class NewsItem:
    """Одна новость, собранная из RSS-ленты.

    ``link`` — канонический URL статьи, используется как ключ дедупликации.
    """

    source: str
    title: str
    link: str
    summary: str = ""
    published_at: datetime | None = None

    @property
    def published_iso(self) -> str:
        """Дата публикации в ISO-формате (или пустая строка)."""
        if self.published_at is None:
            return ""
        return self.published_at.isoformat()


@dataclass(frozen=True, slots=True)
class Digest:
    """Готовый реферат, подготовленный суммаризатором."""

    text: str
    item_count: int
    sources: tuple[str, ...] = ()
    tokens: int = 0


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Результат keyword-фильтрации: прошедшие и отброшенные новости."""

    matched: tuple[NewsItem, ...] = ()
    rejected: tuple[NewsItem, ...] = ()


@dataclass(frozen=True, slots=True)
class CycleResult:
    """Итог одного цикла пайплайна.

    ``new_items`` — сколько новостей ещё не публиковалось (после дедупликации);
    ``selected`` — сколько из них отобрано в дайджест (после фильтра свежести
    и лимита за цикл); ``items`` — сами отобранные новости (заполняется
    только в режиме ``--dry-run``).
    """

    collected: int = 0
    matched: int = 0
    new_items: int = 0
    selected: int = 0
    published: bool = False
    tokens: int = 0
    skipped_reason: str | None = None
    items: tuple[NewsItem, ...] = ()
