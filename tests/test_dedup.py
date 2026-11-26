"""Тесты SQLite-хранилища дедупликации."""

from __future__ import annotations

from pathlib import Path

from digest_bot.dedup import SQLiteStore
from digest_bot.models import NewsItem


def _item(link: str, title: str = "news") -> NewsItem:
    return NewsItem(source="test", title=title, link=link, summary="")


def test_filter_new_returns_everything_on_empty_store(tmp_path: Path) -> None:
    items = [_item("https://example.com/1"), _item("https://example.com/2")]

    with SQLiteStore(tmp_path / "seen.sqlite") as store:
        assert store.filter_new(items) == items


def test_mark_seen_returns_false_for_duplicate(tmp_path: Path) -> None:
    with SQLiteStore(tmp_path / "seen.sqlite") as store:
        assert store.mark_seen("https://example.com/1") is True
        assert store.mark_seen("https://example.com/1") is False


def test_mark_sent_is_idempotent(tmp_path: Path) -> None:
    items = [_item("https://example.com/1")]

    with SQLiteStore(tmp_path / "seen.sqlite") as store:
        assert store.mark_sent(items) == 1
        assert store.mark_sent(items) == 0
        assert store.seen_count() == 1


def test_sent_items_are_not_returned_again(tmp_path: Path) -> None:
    items = [_item("https://example.com/1"), _item("https://example.com/2")]

    with SQLiteStore(tmp_path / "seen.sqlite") as store:
        store.mark_sent(items)
        assert store.filter_new(items) == []


def test_duplicates_inside_one_batch_are_removed(tmp_path: Path) -> None:
    items = [_item("https://example.com/1"), _item("https://example.com/1")]

    with SQLiteStore(tmp_path / "seen.sqlite") as store:
        fresh = store.filter_new(items)

    assert len(fresh) == 1


def test_database_directory_is_created(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "seen.sqlite"

    with SQLiteStore(db_path) as store:
        store.mark_seen("https://example.com/1")
        assert db_path.exists()


def test_store_can_be_reopened(tmp_path: Path) -> None:
    db_path = tmp_path / "seen.sqlite"

    with SQLiteStore(db_path) as store:
        store.mark_sent([_item("https://example.com/1")])

    with SQLiteStore(db_path) as store:
        assert store.filter_new([_item("https://example.com/1")]) == []


def test_usage_without_open_raises(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "seen.sqlite")

    try:
        store.seen_count()
    except RuntimeError as exc:
        assert "не открыт" in str(exc)
    else:  # pragma: no cover - защита от регрессии
        raise AssertionError("ожидалась RuntimeError")
