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


def _legacy_db(path: Path, links: list[str]) -> None:
    """База, созданная старой версией: ключ с www., user_version = 1."""
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE seen (link TEXT PRIMARY KEY, sent_at TIMESTAMP NOT NULL)")
        conn.executemany(
            "INSERT INTO seen (link, sent_at) VALUES (?, ?)",
            [(link, "2026-11-26T16:51:00+00:00") for link in links],
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()


def test_migration_normalizes_legacy_links(tmp_path: Path) -> None:
    # Новости, опубликованные до обновления, лежат под старым ключом (с www.).
    # После переноса они не должны публиковаться заново.
    db_path = tmp_path / "seen.sqlite"
    _legacy_db(db_path, ["https://www.helpnetsecurity.com/2026/11/26/keenadu"])

    with SQLiteStore(db_path) as store:
        assert store.filter_new([_item("https://helpnetsecurity.com/2026/11/26/keenadu")]) == []


def test_migration_keeps_sent_at(tmp_path: Path) -> None:
    db_path = tmp_path / "seen.sqlite"
    _legacy_db(db_path, ["https://www.bleepingcomputer.com/news/security/sys010"])

    with SQLiteStore(db_path) as store:
        rows = store.connection.execute("SELECT link, sent_at FROM seen").fetchall()

    assert len(rows) == 1
    assert rows[0]["link"] == "https://bleepingcomputer.com/news/security/sys010"
    assert rows[0]["sent_at"].startswith("2026-11-26T16:51")


def test_migration_resolves_collision(tmp_path: Path) -> None:
    # Один и тот же сюжет сохранён дважды: под ключом с www. и без.
    db_path = tmp_path / "seen.sqlite"
    _legacy_db(
        db_path,
        [
            "https://www.example.com/a",
            "https://example.com/a",
        ],
    )

    with SQLiteStore(db_path) as store:
        rows = store.connection.execute("SELECT link FROM seen").fetchall()

    assert [row["link"] for row in rows] == ["https://example.com/a"]


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "seen.sqlite"
    _legacy_db(db_path, ["https://www.example.com/a"])

    with SQLiteStore(db_path):
        pass
    with SQLiteStore(db_path) as store:
        version = int(store.connection.execute("PRAGMA user_version").fetchone()[0])
        rows = store.connection.execute("SELECT link FROM seen").fetchall()

    assert version == 2
    assert [row["link"] for row in rows] == ["https://example.com/a"]


def test_fresh_database_has_current_schema_version(tmp_path: Path) -> None:
    with SQLiteStore(tmp_path / "seen.sqlite") as store:
        version = int(store.connection.execute("PRAGMA user_version").fetchone()[0])

    assert version == 2
