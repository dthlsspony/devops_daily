"""Оркестратор пайплайна: сбор → фильтр → дедуп → отбор → порог → реферат → публикация.

Порядок этапов:

1. сбор новостей из лент;
2. фильтр по ключевым словам;
3. дедупликация по ссылке (SQLite);
4. отбор: только свежие новости, не больше ``max_items_per_digest`` за цикл;
5. порог: если отобрано меньше ``min_news_threshold`` — цикл пропускается
   **до** обращения к GigaChat;
6. реферат и публикация; ссылки помечаются обработанными только после успеха.

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
from digest_bot.filters import filter_items, newest_items, recent_items
from digest_bot.models import CycleResult
from digest_bot.publisher.base import Publisher
from digest_bot.summarizer.base import Summarizer

logger = logging.getLogger(__name__)

DRY_RUN_REASON = "dry_run"
THRESHOLD_REASON = "threshold"


class DigestPipeline:
    """Один полный цикл сбора и публикации дайджеста."""

    def __init__(
        self,
        *,
        collector: Collector,
        store: SQLiteStore,
        summarizer: Summarizer | None = None,
        publisher: Publisher | None = None,
        keywords: Sequence[str] = (),
        min_news_threshold: int = 2,
        max_item_age_days: int | None = 3,
        max_items_per_digest: int | None = 30,
        dry_run: bool = False,
    ) -> None:
        self._collector = collector
        self._store = store
        self._summarizer = summarizer
        self._publisher = publisher
        self._keywords = tuple(keywords)
        self._min_news_threshold = min_news_threshold
        self._max_item_age = (
            None if max_item_age_days is None else timedelta(days=max_item_age_days)
        )
        self._max_items_per_digest = max_items_per_digest
        self._dry_run = dry_run

    async def run_cycle(self) -> CycleResult:
        """Выполнить один цикл пайплайна.

        В режиме ``dry_run`` цикл повторяет рабочий до этапа реферата: новости
        возвращаются в ``CycleResult.items`` и помечаются в базе, но GigaChat и
        Telegram не вызываются.
        """
        logger.info("cycle_start dry_run=%s", self._dry_run)

        collected = await self._collector.collect()
        filtered = filter_items(collected, self._keywords)
        new_items = await asyncio.to_thread(self._store.filter_new, filtered.matched)
        selected = newest_items(
            recent_items(new_items, self._max_item_age), self._max_items_per_digest
        )
        base = CycleResult(
            collected=len(collected),
            matched=len(filtered.matched),
            new_items=len(new_items),
            selected=len(selected),
        )
        logger.info(
            "cycle_collected collected=%d matched=%d new=%d selected=%d",
            base.collected,
            base.matched,
            base.new_items,
            base.selected,
        )

        if len(selected) < self._min_news_threshold:
            logger.info(
                "cycle_skipped reason=%s selected=%d threshold=%d",
                THRESHOLD_REASON,
                base.selected,
                self._min_news_threshold,
            )
            return replace(base, skipped_reason=THRESHOLD_REASON)

        if self._dry_run:
            await asyncio.to_thread(self._store.mark_sent, selected)
            return replace(base, skipped_reason=DRY_RUN_REASON, items=tuple(selected))

        summarizer = self._require_summarizer()
        publisher = self._require_publisher()
        digest = await summarizer.summarize(list(selected))
        await publisher.publish(digest)
        marked = await asyncio.to_thread(self._store.mark_sent, selected)
        logger.info(
            "cycle_done items=%d sent=True tokens=%d marked=%d",
            base.selected,
            digest.tokens,
            marked,
        )
        return replace(base, published=True, tokens=digest.tokens)

    def _require_summarizer(self) -> Summarizer:
        if self._summarizer is None:
            raise RuntimeError("Суммаризатор не настроен для рабочего режима")
        return self._summarizer

    def _require_publisher(self) -> Publisher:
        if self._publisher is None:
            raise RuntimeError("Публикатор не настроен для рабочего режима")
        return self._publisher
