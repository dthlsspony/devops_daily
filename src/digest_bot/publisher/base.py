"""Контракт публикатора."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from digest_bot.models import Digest


@runtime_checkable
class Publisher(Protocol):
    """Доставляет готовый дайджест подписчикам."""

    async def publish(self, digest: Digest) -> None:
        """Опубликовать дайджест."""
        ...
