"""Оркестратор пайплайна: сбор → фильтр → дедуп → теги → порог → посты.

Порядок этапов:

1. сбор новостей из лент;
2. фильтр по ключевым словам;
3. дедупликация по ссылке (SQLite);
4. отбор свежих новостей;
5. группировка по тегам: каждая новость получает ровно один тег;
6. отбор постов: не больше ``max_posts_per_cycle`` постов, в каждом — не
   больше ``max_items_per_tag`` новостей;
7. порог: если новых новостей меньше ``min_news_threshold`` — цикл пропускается
   **до** обращения к GigaChat;
8. для каждого поста: реферат и публикация; ссылки помечаются обработанными
   только после успеха.

Модуль связывает этапы между собой; сами этапы ничего не знают друг о друге.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta

from digest_bot.collectors.base import Collector
from digest_bot.dedup import SQLiteStore
from digest_bot.filters import recent_items, select_relevant
from digest_bot.models import CycleResult, NewsItem
from digest_bot.publisher.base import Publisher
from digest_bot.summarizer.base import Summarizer
from digest_bot.tags import CompiledRule, TagRuleLike, compile_rules, group_by_tag

logger = logging.getLogger(__name__)

DRY_RUN_REASON = "dry_run"
THRESHOLD_REASON = "threshold"
NO_PUBLISH_REASON = "nothing_to_publish"

#: Значения по умолчанию для ограничений постов.
DEFAULT_MAX_ITEMS_PER_TAG = 15
DEFAULT_MAX_POSTS_PER_CYCLE = 5
DEFAULT_MIN_ITEMS_PER_TAG = 1


class DigestPipeline:
    """Один полный цикл сбора и публикации дайджестов (по одному посту на тег)."""

    def __init__(
        self,
        *,
        collector: Collector,
        store: SQLiteStore,
        summarizer: Summarizer | None = None,
        publisher: Publisher | None = None,
        keywords: Sequence[str] = (),
        tag_rules: Sequence[TagRuleLike] = (),
        min_news_threshold: int = 2,
        max_item_age_days: int | None = 3,
        max_items_per_tag: int | None = DEFAULT_MAX_ITEMS_PER_TAG,
        max_posts_per_cycle: int | None = DEFAULT_MAX_POSTS_PER_CYCLE,
        min_items_per_tag: int = DEFAULT_MIN_ITEMS_PER_TAG,
        dry_run: bool = False,
    ) -> None:
        self._collector = collector
        self._store = store
        self._summarizer = summarizer
        self._publisher = publisher
        self._keywords = tuple(keywords)
        self._rules: tuple[CompiledRule, ...] = tuple(compile_rules(tag_rules))
        self._min_news_threshold = min_news_threshold
        self._max_item_age = (
            None if max_item_age_days is None else timedelta(days=max_item_age_days)
        )
        self._max_items_per_tag = max_items_per_tag
        self._max_posts_per_cycle = max_posts_per_cycle
        self._min_items_per_tag = min_items_per_tag
        self._dry_run = dry_run

    async def run_cycle(self) -> CycleResult:
        """Выполнить один цикл пайплайна.

        В режиме ``dry_run`` цикл повторяет рабочий до этапа реферата: новости
        возвращаются в ``CycleResult.items`` и помечаются в базе, но GigaChat и
        Telegram не вызываются.
        """
        logger.info("cycle_start dry_run=%s", self._dry_run)

        collected = await self._collector.collect()
        relevant, _ = select_relevant(collected, self._keywords)
        new_items = await asyncio.to_thread(self._store.filter_new, relevant)
        fresh = recent_items(new_items, self._max_item_age)
        posts = self._select_posts(fresh)
        selected = [item for _, items in posts for item in items]

        logger.info(
            "cycle_collected collected=%d relevant=%d new=%d selected=%d posts=%d",
            len(collected),
            len(relevant),
            len(new_items),
            len(selected),
            len(posts),
        )

        if len(new_items) < self._min_news_threshold:
            logger.info(
                "cycle_skipped reason=%s new=%d threshold=%d",
                THRESHOLD_REASON,
                len(new_items),
                self._min_news_threshold,
            )
            return CycleResult(
                collected=len(collected),
                matched=len(relevant),
                new_items=len(new_items),
                skipped_reason=THRESHOLD_REASON,
            )

        if not posts:
            logger.info("cycle_skipped reason=%s new=%d", NO_PUBLISH_REASON, len(new_items))
            return CycleResult(
                collected=len(collected),
                matched=len(relevant),
                new_items=len(new_items),
                skipped_reason=NO_PUBLISH_REASON,
            )

        if self._dry_run:
            for _, items in posts:
                await asyncio.to_thread(self._store.mark_sent, items)
            return CycleResult(
                collected=len(collected),
                matched=len(relevant),
                new_items=len(new_items),
                selected=len(selected),
                posts=len(posts),
                skipped_reason=DRY_RUN_REASON,
                tags=tuple(tag for tag, _ in posts),
                items=tuple(selected),
            )

        summarizer = self._require_summarizer()
        publisher = self._require_publisher()
        tokens = 0
        published_tags: list[str] = []
        for tag, items in posts:
            digest = await summarizer.summarize(items)
            await publisher.publish(digest)
            await asyncio.to_thread(self._store.mark_sent, items)
            tokens += digest.tokens
            published_tags.append(tag)
            logger.info("cycle_post tag=%s items=%d tokens=%d", tag, len(items), digest.tokens)

        logger.info(
            "cycle_done posts=%d items=%d sent=True tokens=%d",
            len(published_tags),
            len(selected),
            tokens,
        )
        return CycleResult(
            collected=len(collected),
            matched=len(relevant),
            new_items=len(new_items),
            selected=len(selected),
            posts=len(published_tags),
            published=True,
            tokens=tokens,
            tags=tuple(published_tags),
        )

    def _select_posts(self, fresh_items: Sequence[NewsItem]) -> list[tuple[str, list[NewsItem]]]:
        """Отобрать посты: теги по очереди, с ограничением по числу и размеру."""
        groups = group_by_tag(fresh_items, self._rules)
        selected: list[tuple[str, list[NewsItem]]] = []
        for tag, items in groups:
            if self._max_posts_per_cycle is not None and len(selected) >= self._max_posts_per_cycle:
                break
            if len(items) < self._min_items_per_tag:
                continue
            take = items
            if self._max_items_per_tag is not None:
                take = items[: self._max_items_per_tag]
            selected.append((tag, [replace(item, tag=tag) for item in take]))
        return selected

    def _require_summarizer(self) -> Summarizer:
        if self._summarizer is None:
            raise RuntimeError("Суммаризатор не настроен для рабочего режима")
        return self._summarizer

    def _require_publisher(self) -> Publisher:
        if self._publisher is None:
            raise RuntimeError("Публикатор не настроен для рабочего режима")
        return self._publisher
