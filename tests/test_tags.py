"""Тесты тематических тегов: классификация и группировка."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from digest_bot.config import TagRule
from digest_bot.models import NewsItem
from digest_bot.tags import (
    DEFAULT_TAG,
    CompiledRule,
    classify,
    compile_rules,
    group_by_tag,
    tag_icon,
)

RULES = (
    TagRule(tag="supply-chain", keywords=["supply chain", "malicious package"]),
    TagRule(tag="malware", keywords=["ransomware", "backdoor"]),
    TagRule(tag="php", keywords=["php", "composer"]),
    TagRule(tag="nodejs", keywords=["node.js", "npm"], sources=["nodejs"]),
    TagRule(tag="exploits", keywords=["rce", "actively exploited"]),
)


def _item(title: str, source: str = "thehackernews", summary: str = "") -> NewsItem:
    return NewsItem(
        source=source,
        title=title,
        link=f"https://example.com/{title}",
        summary=summary,
    )


def _rules() -> list[CompiledRule]:
    return compile_rules(RULES)


def test_stack_keyword_wins() -> None:
    assert classify(_item("Critical RCE in PHP"), _rules()) == "php"


def test_distinctive_type_wins_over_stack() -> None:
    # Supply chain — более отличительный признак, чем стек.
    assert classify(_item("Malicious package found in npm ecosystem"), _rules()) == "supply-chain"


def test_source_rule_is_a_fallback() -> None:
    # В заголовке нет ни слова "node.js", но источник — фид Node.js.
    assert (
        classify(_item("Wednesday, November 11, 2026 Security Releases", "nodejs"), _rules())
        == "nodejs"
    )


def test_summary_does_not_influence_the_tag() -> None:
    # Классификация по заголовку: иначе случайное слово в описании уводит новость.
    item = _item("Microsoft confirms authentication issues", summary="A patch for the linux flaw")

    assert classify(item, _rules()) == DEFAULT_TAG


def test_unmatched_news_gets_the_default_tag() -> None:
    assert classify(_item("Company announces office opening"), _rules()) == DEFAULT_TAG


def test_rule_order_decides_the_winner() -> None:
    rules = compile_rules(
        (
            TagRule(tag="first", keywords=["flaw"]),
            TagRule(tag="second", keywords=["flaw"]),
        )
    )

    assert classify(_item("A flaw in the parser"), rules) == "first"


def test_group_by_tag_orders_by_size_then_name() -> None:
    items = [
        _item("PHP RCE one"),
        _item("PHP RCE two"),
        _item("npm RCE three"),
        _item("Ransomware outbreak"),
    ]

    groups = group_by_tag(items, _rules())

    # php — две новости; дальше по одной: алфавитный порядок тегов.
    assert [tag for tag, _ in groups] == ["php", "malware", "nodejs"]
    assert [len(news) for _, news in groups] == [2, 1, 1]


def test_default_tag_goes_last() -> None:
    # Общая подборка не должна вытеснять специфичные теги из постов.
    items = [
        _item("Company opens a new office"),
        _item("Another press release about nothing"),
        _item("PHP RCE one"),
    ]

    groups = group_by_tag(items, _rules())

    assert [tag for tag, _ in groups] == ["php", DEFAULT_TAG]


def test_group_by_tag_sorts_news_from_newest_to_oldest() -> None:
    now = datetime.now(UTC)
    items = [
        NewsItem(
            source="s",
            title="old",
            link="https://example.com/old",
            published_at=now - timedelta(days=2),
        ),
        NewsItem(source="s", title="new", link="https://example.com/new", published_at=now),
        NewsItem(source="s", title="no date", link="https://example.com/none"),
    ]

    _, news = group_by_tag(items, _rules())[0]

    assert [item.title for item in news] == ["new", "old", "no date"]


def test_tag_icon_is_known_for_every_default_tag() -> None:
    assert tag_icon("php") == "🐘"
    assert tag_icon("нет-такого") == tag_icon(DEFAULT_TAG)
