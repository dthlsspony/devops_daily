"""Контракт сборщика новостей."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from digest_bot.models import NewsItem


@runtime_checkable
class Collector(Protocol):
    """Источник новостей.

    Реализация не знает о существовании соседних этапов пайплайна.
    """

    async def collect(self) -> list[NewsItem]:
        """Вернуть список собранных новостей."""
        ...
