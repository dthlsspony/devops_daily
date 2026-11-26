"""Тесты фильтров отбора: ключевые слова, свежесть, лимит."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from digest_bot.filters import (
    compile_keywords,
    filter_items,
    matches_keywords,
    newest_items,
    recent_items,
)
from digest_bot.models import NewsItem

_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _item(title: str, summary: str = "") -> NewsItem:
    return NewsItem(
        source="test",
        title=title,
        link=f"https://example.com/{abs(hash(title))}",
        summary=summary,
    )


def _dated(title: str, moment: datetime) -> NewsItem:
    return NewsItem(
        source="test",
        title=title,
        link=f"https://example.com/{abs(hash(title))}",
        summary="",
        published_at=moment,
    )


def test_keyword_matches_title() -> None:
    result = filter_items([_item("Critical RCE in PHP")], ["rce", "php"])

    assert len(result.matched) == 1
    assert result.rejected == ()


def test_keyword_matching_is_case_insensitive() -> None:
    result = filter_items([_item("cVe-2024-1234 is exploitable")], ["cve"])

    assert len(result.matched) == 1


def test_keyword_matches_summary() -> None:
    item = _item("Weekly roundup", summary="The release fixes an nginx vulnerability")

    result = filter_items([item], ["nginx"])

    assert len(result.matched) == 1


def test_unrelated_item_is_rejected() -> None:
    item = _item("How to choose a cloud provider")

    result = filter_items([item], ["php", "linux", "cve"])

    assert result.matched == ()
    assert len(result.rejected) == 1


def test_any_keyword_is_enough() -> None:
    items = [_item("PHP 8.3.2 released"), _item("Marketing news"), _item("nginx update")]

    result = filter_items(items, ["php", "nginx"])

    assert [item.title for item in result.matched] == ["PHP 8.3.2 released", "nginx update"]


def test_keyword_does_not_match_inside_a_word() -> None:
    item = _item("Source code of the project leaked")

    result = filter_items([item], ["rce"])

    assert result.matched == ()


def test_keyword_matches_identifier_with_suffix() -> None:
    item = _item("CVE-2024-22243 allows account takeover")

    result = filter_items([item], ["cve"])

    assert len(result.matched) == 1


def test_keyword_matches_plural_form() -> None:
    items = [
        _item("Several critical vulnerabilities were fixed"),
        _item("New exploits target nginx"),
        _item("Security patches released"),
    ]

    result = filter_items(items, ["vulnerability", "exploit", "security patch"])

    assert [item.title for item in result.matched] == [item.title for item in items]


def test_keyword_matches_before_version_digit() -> None:
    result = filter_items([_item("Critical bug fixed in PHP8 release")], ["php"])

    assert len(result.matched) == 1


def test_keyword_does_not_match_longer_word() -> None:
    result = filter_items([_item("PHPStan 2.0 released")], ["php"])

    assert result.matched == ()


def test_dotted_keyword_matches_dotted_source() -> None:
    item = _item("Update for node.js releases")

    result = filter_items([item], ["node.js"])

    assert len(result.matched) == 1


def test_empty_keyword_list_matches_nothing() -> None:
    result = filter_items([_item("PHP release")], [])

    assert result.matched == ()
    assert len(result.rejected) == 1


def test_blank_keywords_are_ignored() -> None:
    result = filter_items([_item("PHP release")], ["   ", ""])

    assert result.matched == ()


def test_matches_keywords_helper() -> None:
    patterns = compile_keywords(["php"])

    assert matches_keywords(_item("PHP release"), patterns) is True
    assert matches_keywords(_item("Nothing here"), patterns) is False


def test_recent_items_drop_old_news() -> None:
    fresh = _dated("Fresh CVE", _NOW - timedelta(days=2))
    stale = _dated("Stale CVE", _NOW - timedelta(days=4))
    undated = _item("Undated news")

    kept = recent_items([fresh, stale, undated], timedelta(days=3), _NOW)

    assert kept == [fresh, undated]


def test_recent_items_can_be_disabled() -> None:
    stale = _dated("Stale CVE", datetime(2020, 1, 1, tzinfo=UTC))

    assert recent_items([stale], None, _NOW) == [stale]


def test_recent_items_treat_naive_time_as_utc() -> None:
    naive = _dated("Naive CVE", datetime(2026, 1, 14, 12, 0))

    assert recent_items([naive], timedelta(days=1), _NOW) == [naive]


def test_newest_items_keep_freshest() -> None:
    oldest = _dated("oldest", _NOW - timedelta(days=3))
    middle = _dated("middle", _NOW - timedelta(days=2))
    newest = _dated("newest", _NOW - timedelta(days=1))

    assert newest_items([oldest, middle, newest], 2) == [newest, middle]


def test_newest_items_put_undated_last() -> None:
    undated = _item("Undated news")
    dated = _dated("Dated CVE", _NOW - timedelta(days=5))

    assert newest_items([undated, dated], 1) == [dated]


def test_newest_items_without_limit_keep_order() -> None:
    items = [_item("first CVE"), _item("second CVE")]

    assert newest_items(items, None) == items
    assert newest_items(items, 5) == items
