"""Тесты оркестратора пайплайна."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from digest_bot.dedup import SQLiteStore
from digest_bot.models import Digest, NewsItem
from digest_bot.pipeline import DigestPipeline


class FakeCollector:
    """Сборщик с заранее заданным списком новостей."""

    def __init__(self, items: list[NewsItem]) -> None:
        self._items = items
        self.calls = 0

    async def collect(self) -> list[NewsItem]:
        self.calls += 1
        return list(self._items)


class FakeSummarizer:
    """Суммаризатор, запоминающий вызовы."""

    def __init__(self) -> None:
        self.calls: list[list[NewsItem]] = []

    async def summarize(self, items: list[NewsItem]) -> Digest:
        self.calls.append(list(items))
        return Digest(
            text="## Главное\n- дайджест",
            item_count=len(items),
            sources=("thehackernews",),
            tokens=42,
        )


class FakePublisher:
    """Публикатор, запоминающий отправленные дайджесты."""

    def __init__(self) -> None:
        self.digests: list[Digest] = []

    async def publish(self, digest: Digest) -> None:
        self.digests.append(digest)


class FailingSummarizer:
    """Суммаризатор, который всегда падает."""

    async def summarize(self, items: list[NewsItem]) -> Digest:
        raise RuntimeError("gigachat unavailable")


class FailingPublisher(FakePublisher):
    """Публикатор, который всегда падает."""

    async def publish(self, digest: Digest) -> None:
        raise RuntimeError("telegram unavailable")


def _items(count: int) -> list[NewsItem]:
    return [
        NewsItem(
            source="thehackernews",
            title=f"Critical RCE {index}",
            link=f"https://example.com/{index}",
            summary="exploit",
        )
        for index in range(count)
    ]


def _dated(age: timedelta) -> datetime:
    return datetime.now(UTC) - age


def _open_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "seen.sqlite")
    store.open()
    return store


def _pipeline(
    tmp_path: Path,
    collector: FakeCollector,
    summarizer: FakeSummarizer | None = None,
    publisher: FakePublisher | None = None,
    *,
    threshold: int = 2,
    dry_run: bool = False,
) -> DigestPipeline:
    return DigestPipeline(
        collector=collector,
        store=_open_store(tmp_path),
        summarizer=summarizer,
        publisher=publisher,
        keywords=["rce"],
        min_news_threshold=threshold,
        dry_run=dry_run,
    )


async def test_few_news_are_skipped(tmp_path: Path) -> None:
    summarizer = FakeSummarizer()
    publisher = FakePublisher()
    pipeline = _pipeline(
        tmp_path,
        FakeCollector(_items(1)),
        summarizer,
        publisher,
        threshold=2,
    )

    result = await pipeline.run_cycle()

    assert publisher.digests == []
    assert summarizer.calls == []
    assert result.published is False
    assert result.skipped_reason == "threshold"
    assert result.new_items == 1


async def test_enough_news_are_published_once(tmp_path: Path) -> None:
    summarizer = FakeSummarizer()
    publisher = FakePublisher()
    pipeline = _pipeline(tmp_path, FakeCollector(_items(3)), summarizer, publisher, threshold=2)

    result = await pipeline.run_cycle()

    assert len(publisher.digests) == 1
    assert len(summarizer.calls) == 1
    assert result.published is True
    assert result.new_items == 3
    assert result.tokens == 42


async def test_published_items_are_not_repeated(tmp_path: Path) -> None:
    publisher = FakePublisher()
    store = SQLiteStore(tmp_path / "seen.sqlite")
    store.open()
    collector = FakeCollector(_items(3))

    pipeline = DigestPipeline(
        collector=collector,
        store=store,
        summarizer=FakeSummarizer(),
        publisher=publisher,
        keywords=["rce"],
        min_news_threshold=2,
    )
    first = await pipeline.run_cycle()
    second = await pipeline.run_cycle()

    assert first.published is True
    assert second.published is False
    assert second.new_items == 0
    assert len(publisher.digests) == 1


async def test_failed_publish_keeps_items_for_next_cycle(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "seen.sqlite")
    store.open()
    collector = FakeCollector(_items(3))

    pipeline = DigestPipeline(
        collector=collector,
        store=store,
        summarizer=FakeSummarizer(),
        publisher=FailingPublisher(),
        keywords=["rce"],
        min_news_threshold=2,
    )

    with pytest.raises(RuntimeError):
        await pipeline.run_cycle()

    assert store.filter_new(_items(3)) == _items(3)


async def test_failed_summary_keeps_items_for_next_cycle(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "seen.sqlite")
    store.open()
    publisher = FakePublisher()

    pipeline = DigestPipeline(
        collector=FakeCollector(_items(3)),
        store=store,
        summarizer=FailingSummarizer(),
        publisher=publisher,
        keywords=["rce"],
        min_news_threshold=2,
    )

    with pytest.raises(RuntimeError):
        await pipeline.run_cycle()

    assert publisher.digests == []
    assert store.filter_new(_items(3)) == _items(3)


async def test_dry_run_returns_items_without_external_calls(tmp_path: Path) -> None:
    summarizer = FakeSummarizer()
    publisher = FakePublisher()
    pipeline = _pipeline(
        tmp_path,
        FakeCollector(_items(3)),
        summarizer,
        publisher,
        dry_run=True,
    )

    result = await pipeline.run_cycle()

    assert publisher.digests == []
    assert summarizer.calls == []
    assert result.published is False
    assert result.skipped_reason == "dry_run"
    assert result.items == tuple(_items(3))
    # Как в рабочем режиме, новости помечаются — второй прогон покажет 0 новых.
    second = await pipeline.run_cycle()
    assert second.new_items == 0
    assert second.skipped_reason == "threshold"


async def test_dry_run_below_threshold_marks_nothing(tmp_path: Path) -> None:
    store = _open_store(tmp_path)
    publisher = FakePublisher()
    pipeline = DigestPipeline(
        collector=FakeCollector(_items(1)),
        store=store,
        summarizer=FakeSummarizer(),
        publisher=publisher,
        keywords=["rce"],
        min_news_threshold=5,
        dry_run=True,
    )

    result = await pipeline.run_cycle()

    assert publisher.digests == []
    assert result.skipped_reason == "threshold"
    assert result.items == ()
    assert store.filter_new(_items(1)) == _items(1)


def _aged_items(ages: list[timedelta]) -> list[NewsItem]:
    return [
        NewsItem(
            source="thehackernews",
            title=f"Critical RCE {index}",
            link=f"https://example.com/{index}",
            summary="exploit",
            published_at=_dated(age),
        )
        for index, age in enumerate(ages)
    ]


async def test_stale_items_are_not_digestible(tmp_path: Path) -> None:
    items = _aged_items([timedelta(days=10), timedelta(days=6), timedelta(days=5)])
    publisher = FakePublisher()
    pipeline = _pipeline(tmp_path, FakeCollector(items), FakeSummarizer(), publisher)

    result = await pipeline.run_cycle()

    assert result.new_items == 3
    assert result.selected == 0
    assert result.skipped_reason == "threshold"
    assert publisher.digests == []


async def test_digest_is_capped_to_freshest_items(tmp_path: Path) -> None:
    # Самый свежий — последний в ленте: лимит должен взять его, а не первый попавшийся.
    items = _aged_items(
        [
            timedelta(hours=5),
            timedelta(hours=4),
            timedelta(hours=3),
            timedelta(hours=2),
            timedelta(hours=1),
        ]
    )
    store = _open_store(tmp_path)
    summarizer = FakeSummarizer()
    publisher = FakePublisher()
    pipeline = DigestPipeline(
        collector=FakeCollector(items),
        store=store,
        summarizer=summarizer,
        publisher=publisher,
        keywords=["rce"],
        min_news_threshold=2,
        max_items_per_digest=3,
    )

    result = await pipeline.run_cycle()

    assert result.published is True
    assert result.selected == 3
    summarized_links = [item.link for item in summarizer.calls[0]]
    assert summarized_links == [item.link for item in reversed(items[-3:])]
    # Более старые не помечены и попадут в следующий цикл.
    assert store.filter_new(items) == items[:2]


async def test_unrelated_news_are_filtered_out(tmp_path: Path) -> None:
    items = [
        NewsItem(
            source="blog",
            title="How to choose a cloud provider",
            link="https://example.com/cloud",
            summary="hosting",
        )
    ]
    publisher = FakePublisher()
    pipeline = _pipeline(tmp_path, FakeCollector(items), FakeSummarizer(), publisher, threshold=1)

    result = await pipeline.run_cycle()

    assert result.matched == 0
    assert publisher.digests == []
    assert result.skipped_reason == "threshold"
