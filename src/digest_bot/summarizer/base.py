"""Контракт суммаризатора."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from digest_bot.models import Digest, NewsItem


@runtime_checkable
class Summarizer(Protocol):
    """Превращает список новостей в связный реферат."""

    async def summarize(self, items: list[NewsItem]) -> Digest:
        """Вернуть реферат по переданным новостям."""
        ...
