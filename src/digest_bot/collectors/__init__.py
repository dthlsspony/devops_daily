"""Сбор новостей из RSS-лент."""

from __future__ import annotations

from digest_bot.collectors.base import Collector
from digest_bot.collectors.rss import RSSCollector

__all__ = ["Collector", "RSSCollector"]
