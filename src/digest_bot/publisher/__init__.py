"""Публикация дайджеста."""

from __future__ import annotations

from digest_bot.publisher.base import Publisher
from digest_bot.publisher.telegram import TelegramPublisher

__all__ = ["Publisher", "TelegramPublisher"]
