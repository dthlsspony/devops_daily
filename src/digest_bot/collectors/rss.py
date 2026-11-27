"""Сборщик новостей из RSS/Atom-лент.

Падение одного фида не ломает цикл: ошибка логируется и лента пропускается.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from digest_bot.config import Feed
from digest_bot.models import NewsItem
from digest_bot.urls import canonical_link

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "SecurityFeedBot/1.0"
SUMMARY_LIMIT = 500

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    """Привести произвольное значение к обрезанной строке."""
    return str(value).strip()


def clean_summary(value: Any) -> str:
    """Убрать HTML-теги и схлопнуть пробелы в описании новости."""
    text = _TAG_RE.sub(" ", str(value))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if len(text) > SUMMARY_LIMIT:
        text = text[: SUMMARY_LIMIT - 1].rstrip() + "…"
    return text


def parse_published(entry: Any) -> datetime | None:
    """Извлечь дату публикации из записи ленты."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed_time = entry.get(key)
        if not parsed_time:
            continue
        try:
            year, month, day, hour, minute, second = parsed_time[:6]
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        except (TypeError, ValueError):
            continue
    return None


def build_items(source: str, parsed: Any, trusted: bool = False) -> list[NewsItem]:
    """Преобразовать разобранную ленту в список новостей."""
    entries = parsed.get("entries") or []
    items: list[NewsItem] = []
    for entry in entries:
        title = clean_text(entry.get("title") or "")
        link = canonical_link(clean_text(entry.get("link") or ""))
        if not title or not link:
            continue
        summary = clean_summary(entry.get("summary") or entry.get("description") or "")
        items.append(
            NewsItem(
                source=source,
                title=title,
                link=link,
                summary=summary,
                published_at=parse_published(entry),
                trusted=trusted,
            )
        )
    return items


class RSSCollector:
    """Собирает новости из набора RSS-лент через ``httpx``.

    ``max_items_per_feed`` ограничивает число записей, взятых из одной ленты
    (ленты обычно упорядочены от новых к старым). Без лимита фид-архив из
    тысячи записей попадал бы в фильтр целиком.
    """

    def __init__(
        self,
        feeds: Sequence[Feed],
        client: httpx.AsyncClient,
        *,
        timeout: float = 20.0,
        user_agent: str = DEFAULT_USER_AGENT,
        max_items_per_feed: int | None = None,
    ) -> None:
        self._feeds = tuple(feeds)
        self._client = client
        self._timeout = timeout
        self._user_agent = user_agent
        self._max_items_per_feed = max_items_per_feed

    async def collect(self) -> list[NewsItem]:
        """Собрать новости со всех лент."""
        items: list[NewsItem] = []
        for feed in self._feeds:
            items.extend(await self._collect_feed(feed))
        return items

    async def _collect_feed(self, feed: Feed) -> list[NewsItem]:
        try:
            response = await self._client.get(
                feed.url,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "feed_fetch_failed source=%s url=%s error=%s",
                feed.source,
                feed.url,
                type(exc).__name__,
            )
            return []
        parsed: Any = await asyncio.to_thread(feedparser.parse, response.content)
        items = build_items(feed.source, parsed, feed.trusted)
        if self._max_items_per_feed is not None:
            items = items[: self._max_items_per_feed]
        logger.info("feed_collected source=%s items=%d", feed.source, len(items))
        return items
