"""Тесты оркестратора пайплайна."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from digest_bot.config import TagRule
from digest_bot.dedup import SQLiteStore
from digest_bot.models import Digest, NewsItem
from digest_bot.pipeline import (
    DEFAULT_MAX_ITEMS_PER_TAG,
    DEFAULT_MAX_POSTS_PER_CYCLE,
    DEFAULT_MIN_ITEMS_PER_TAG,
    DigestPipeline,
)


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
            tag=items[0].tag if items else "",
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


#: Правила тегирования для тестов: php по слову "php", nodejs по источнику.
TEST_TAG_RULES = (
    TagRule(tag="php", keywords=["php"]),
    TagRule(tag="nodejs", sources=["nodejs"]),
)


def _pipeline(
    tmp_path: Path,
    collector: FakeCollector,
    summarizer: FakeSummarizer | None = None,
    publisher: FakePublisher | None = None,
    *,
    threshold: int = 2,
    dry_run: bool = False,
    tag_rules: tuple[TagRule, ...] = TEST_TAG_RULES,
    max_items_per_tag: int | None = DEFAULT_MAX_ITEMS_PER_TAG,
    max_posts_per_cycle: int | None = DEFAULT_MAX_POSTS_PER_CYCLE,
    min_items_per_tag: int = DEFAULT_MIN_ITEMS_PER_TAG,
) -> DigestPipeline:
    return DigestPipeline(
        collector=collector,
        store=_open_store(tmp_path),
        summarizer=summarizer,
        publisher=publisher,
        keywords=["rce"],
        tag_rules=tag_rules,
        min_news_threshold=threshold,
        max_items_per_tag=max_items_per_tag,
        max_posts_per_cycle=max_posts_per_cycle,
        min_items_per_tag=min_items_per_tag,
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
    assert [item.link for item in result.items] == [item.link for item in _items(3)]
    assert {item.tag for item in result.items} == {"прочее"}
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
    # Новости есть, но все несвежие — публиковать нечего.
    assert result.skipped_reason == "nothing_to_publish"
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
        max_items_per_tag=3,
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


def _tagged(source: str, title: str, index: int) -> NewsItem:
    """Новость с заданным источником и заголовком."""
    return NewsItem(
        source=source,
        title=title,
        link=f"https://example.com/{source}/{index}",
        summary="exploit",
        published_at=datetime.now(UTC) - timedelta(hours=index),
    )


async def test_each_tag_gets_its_own_post(tmp_path: Path) -> None:
    items = [
        _tagged("thehackernews", "PHP session RCE allows code execution", 1),
        _tagged("thehackernews", "PHP image parser RCE in image upload", 2),
        _tagged("nodejs", "Critical RCE in parser", 3),
    ]
    summarizer = FakeSummarizer()
    publisher = FakePublisher()
    pipeline = _pipeline(tmp_path, FakeCollector(items), summarizer, publisher, threshold=2)

    result = await pipeline.run_cycle()

    # Два поста: по тегу php (2 новости) и по тегу nodejs (1 новость).
    assert result.posts == 2
    assert result.tags == ("php", "nodejs")
    assert len(publisher.digests) == 2
    assert [digest.tag for digest in publisher.digests] == ["php", "nodejs"]
    assert publisher.digests[0].item_count == 2
    assert publisher.digests[1].item_count == 1


async def test_news_is_published_exactly_once(tmp_path: Path) -> None:
    items = [
        _tagged("thehackernews", "PHP session RCE allows code execution", 1),
        _tagged("thehackernews", "PHP image parser RCE in image upload", 2),
        _tagged("nodejs", "Critical RCE in parser", 3),
    ]
    summarizer = FakeSummarizer()
    publisher = FakePublisher()
    pipeline = _pipeline(tmp_path, FakeCollector(items), summarizer, publisher, threshold=2)

    await pipeline.run_cycle()

    published_links = [item.link for call in summarizer.calls for item in call]
    assert len(published_links) == len(set(published_links)) == 3


async def test_posts_per_cycle_are_capped(tmp_path: Path) -> None:
    items = [
        _tagged("thehackernews", "PHP session flaw allows RCE", 1),
        _tagged("nodejs", "Critical RCE in parser", 2),
        _tagged("thehackernews", "Another PHP flaw", 3),
    ]
    summarizer = FakeSummarizer()
    publisher = FakePublisher()
    pipeline = _pipeline(
        tmp_path,
        FakeCollector(items),
        summarizer,
        publisher,
        threshold=1,
        max_posts_per_cycle=1,
    )

    result = await pipeline.run_cycle()

    assert result.posts == 1
    assert len(publisher.digests) == 1


async def test_tag_with_too_few_items_waits_for_next_cycle(tmp_path: Path) -> None:
    items = [
        _tagged("thehackernews", "PHP session RCE allows code execution", 1),
        _tagged("thehackernews", "PHP image parser RCE in image upload", 2),
        _tagged("nodejs", "Critical RCE in parser", 3),
    ]
    store = _open_store(tmp_path)
    publisher = FakePublisher()
    pipeline = DigestPipeline(
        collector=FakeCollector(items),
        store=store,
        summarizer=FakeSummarizer(),
        publisher=publisher,
        keywords=["rce"],
        tag_rules=TEST_TAG_RULES,
        min_news_threshold=1,
        min_items_per_tag=2,
    )

    result = await pipeline.run_cycle()

    # Пост только у php: у nodejs одна новость, порог тега не достигнут.
    assert result.posts == 1
    assert result.tags == ("php",)
    # Новость nodejs не помечена и дождётся следующего цикла.
    assert [item.link for item in store.filter_new(items)] == [items[2].link]


async def test_items_beyond_the_tag_limit_stay_unpublished(tmp_path: Path) -> None:
    items = [_tagged("thehackernews", f"PHP RCE number {index}", index) for index in range(1, 5)]
    store = _open_store(tmp_path)
    publisher = FakePublisher()
    pipeline = DigestPipeline(
        collector=FakeCollector(items),
        store=store,
        summarizer=FakeSummarizer(),
        publisher=publisher,
        keywords=["rce"],
        tag_rules=TEST_TAG_RULES,
        min_news_threshold=1,
        max_items_per_tag=2,
    )

    result = await pipeline.run_cycle()

    assert result.selected == 2
    assert len(store.filter_new(items)) == 2


async def test_dry_run_reports_posts_per_tag(tmp_path: Path) -> None:
    items = [
        _tagged("thehackernews", "PHP session RCE allows code execution", 1),
        _tagged("nodejs", "Critical RCE in parser", 2),
    ]
    publisher = FakePublisher()
    pipeline = _pipeline(
        tmp_path, FakeCollector(items), FakeSummarizer(), publisher, threshold=1, dry_run=True
    )

    result = await pipeline.run_cycle()

    assert publisher.digests == []
    assert result.posts == 2
    # Обе новости по одной: порядок тегов при равенстве — алфавитный.
    assert result.tags == ("nodejs", "php")
    assert {item.tag for item in result.items} == {"php", "nodejs"}


async def test_folded_duplicate_is_marked_with_its_keeper(tmp_path: Path) -> None:
    # Один сюжет в двух изданиях: публикуется один раз, второй закрывается
    # вместе с ним — иначе он всплыл бы в следующем цикле.
    kept = _tagged("thehackernews", "1Password customer data stolen after GitHub breach", 1)
    duplicate = _tagged(
        "bleepingcomputer", "1Password customer data stolen after employee GitHub breach", 2
    )
    store = _open_store(tmp_path)
    publisher = FakePublisher()
    pipeline = DigestPipeline(
        collector=FakeCollector([kept, duplicate]),
        store=store,
        summarizer=FakeSummarizer(),
        publisher=publisher,
        keywords=["breach"],
        tag_rules=TEST_TAG_RULES,
        min_news_threshold=1,
    )

    result = await pipeline.run_cycle()

    assert result.selected == 1
    assert len(publisher.digests) == 1
    # Дубликат помечен обработанным вместе с основной новостью.
    assert store.filter_new([kept, duplicate]) == []


async def test_duplicate_is_kept_when_nothing_published(tmp_path: Path) -> None:
    # Пока пост не опубликован, дубликат остаётся необработанным.
    kept = _tagged("thehackernews", "1Password customer data stolen after GitHub breach", 1)
    duplicate = _tagged(
        "bleepingcomputer", "1Password customer data stolen after employee GitHub breach", 2
    )
    store = _open_store(tmp_path)
    publisher = FakePublisher()
    pipeline = DigestPipeline(
        collector=FakeCollector([kept, duplicate]),
        store=store,
        summarizer=FakeSummarizer(),
        publisher=publisher,
        keywords=["breach"],
        tag_rules=TEST_TAG_RULES,
        min_news_threshold=5,
    )

    result = await pipeline.run_cycle()

    assert result.published is False
    assert store.filter_new([kept, duplicate]) == [kept, duplicate]


async def test_news_published_before_an_update_is_not_republished(tmp_path: Path) -> None:
    # Сценарий: программа обновилась, формат ключа дедупликации изменился
    # (убран www.). Новость, опубликованная до обновления, лежит в базе под
    # старым ключом — после переноса она не должна публиковаться заново.
    db_path = tmp_path / "seen.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE seen (link TEXT PRIMARY KEY, sent_at TIMESTAMP NOT NULL)")
        conn.execute(
            "INSERT INTO seen (link, sent_at) VALUES (?, ?)",
            ("https://www.helpnetsecurity.com/2026/11/26/keenadu", "2026-11-26T16:51:00+00:00"),
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    items = [
        NewsItem(
            source="helpnetsecurity",
            title="Keenadu malware preinstalled on Android devices",
            link="https://helpnetsecurity.com/2026/11/26/keenadu",
            summary="malware",
        )
    ]
    publisher = FakePublisher()
    store = SQLiteStore(db_path)
    store.open()
    pipeline = DigestPipeline(
        collector=FakeCollector(items),
        store=store,
        summarizer=FakeSummarizer(),
        publisher=publisher,
        keywords=["malware"],
        min_news_threshold=1,
    )

    result = await pipeline.run_cycle()

    assert publisher.digests == []
    assert result.new_items == 0
