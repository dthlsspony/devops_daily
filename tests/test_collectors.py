"""Тесты сборщика RSS на реальных снимках лент (см. ``fixtures/README.md``).

Снимки статичны, поэтому проверки точные: они фиксируют и разбор реального
формата, и то, что фикстура — тот самый файл.
"""

from __future__ import annotations

from datetime import UTC, datetime

import feedparser
import httpx
import pytest
import respx

from digest_bot.collectors.rss import RSSCollector, build_items, canonical_link, clean_summary
from digest_bot.config import DEFAULT_KEYWORDS, Feed
from digest_bot.filters import filter_items, select_relevant

FEED_URL = "https://example.com/feed.xml"
BROKEN_URL = "https://example.com/broken.xml"


def _collector(url: str, client: httpx.AsyncClient, source: str = "feed") -> RSSCollector:
    return RSSCollector([Feed(source=source, url=url)], client)


@respx.mock
async def test_collect_parses_real_hackernews_feed(hnr_feed_bytes: bytes) -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=hnr_feed_bytes))

    async with httpx.AsyncClient() as client:
        items = await _collector(FEED_URL, client, "thehackernews").collect()

    assert len(items) == 50
    first = items[0]
    assert first.source == "thehackernews"
    assert first.title == (
        "13 Malicious NuGet Packages Turn Fake .NET Job Assessments Into Malware Delivery Traps"
    )
    assert (
        first.link == "https://thehackernews.com/2026/11/13-malicious-nuget-packages-turn-fake.html"
    )
    assert first.published_at == datetime(2026, 11, 26, 3, 48, 52, tzinfo=UTC)
    assert first.summary.startswith(
        "Cybersecurity researchers have discovered 13 malicious NuGet packages"
    )
    assert all(item.published_at is not None for item in items)
    # Лента упорядочена от новых к старым — на это опирается лимит записей на фид.
    newest, oldest = items[0].published_at, items[-1].published_at
    assert newest is not None and oldest is not None
    assert newest > oldest


@respx.mock
async def test_collect_parses_real_bleepingcomputer_feed(bleeping_feed_bytes: bytes) -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=bleeping_feed_bytes))

    async with httpx.AsyncClient() as client:
        items = await _collector(FEED_URL, client, "bleepingcomputer").collect()

    assert len(items) == 15
    assert (
        items[0].title == "Palo Alto Networks warns of Fragnesia flaw exploited in PAN-OS attacks"
    )
    assert (
        items[0].link
        == "https://bleepingcomputer.com/news/security/palo-alto-networks-warns-of-fragnesia-flaw-exploited-in-pan-os-attacks"
    )


@respx.mock
async def test_atom_feed_is_supported(php_feed_bytes: bytes) -> None:
    # php.net отдаёт Atom 1.0, а не RSS 2.0.
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=php_feed_bytes))

    async with httpx.AsyncClient() as client:
        items = await _collector(FEED_URL, client, "php.net").collect()

    assert [item.title for item in items] == [
        "PHP 8.6.0 released!",
        "PHP 8.5.13 released!",
        "PHP 8.4.28 released!",
        "PHP 8.3.37 released!",
        "PHP 8.2.36 released!",
    ]
    assert items[0].link == "https://php.net/releases/8_6_0.php"
    assert items[0].published_at == datetime(2026, 11, 19, tzinfo=UTC)


@respx.mock
async def test_feed_without_descriptions_is_parsed_by_title(node_feed_bytes: bytes) -> None:
    # Фид security-advisories Node.js — RSS 2.0 без <description>:
    # новость можно отобрать только по заголовку.
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=node_feed_bytes))

    async with httpx.AsyncClient() as client:
        items = await _collector(FEED_URL, client, "nodejs").collect()

    assert len(items) == 78
    assert items[0].title == "Wednesday, November 11, 2026 Security Releases"
    assert all(item.summary == "" for item in items)
    assert all(item.published_at is not None for item in items)


@respx.mock
async def test_broken_feed_does_not_break_cycle(hnr_feed_bytes: bytes) -> None:
    respx.get(BROKEN_URL).mock(return_value=httpx.Response(500))
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=hnr_feed_bytes))

    async with httpx.AsyncClient() as client:
        collector = RSSCollector(
            [Feed(source="broken", url=BROKEN_URL), Feed(source="thehackernews", url=FEED_URL)],
            client,
        )
        items = await collector.collect()

    assert {item.source for item in items} == {"thehackernews"}
    assert len(items) == 50


@respx.mock
async def test_timeout_is_treated_as_empty_feed() -> None:
    respx.get(FEED_URL).mock(side_effect=httpx.TimeoutException("boom"))

    async with httpx.AsyncClient() as client:
        items = await _collector(FEED_URL, client).collect()

    assert items == []


@respx.mock
async def test_collector_sends_user_agent(hnr_feed_bytes: bytes) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["user-agent"] = request.headers.get("user-agent", "")
        return httpx.Response(200, content=hnr_feed_bytes)

    respx.get(FEED_URL).mock(side_effect=_capture)

    async with httpx.AsyncClient() as client:
        await _collector(FEED_URL, client).collect()

    assert captured["user-agent"] == "SecurityFeedBot/1.0"


@respx.mock
async def test_collector_limits_items_per_feed(hnr_feed_bytes: bytes) -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=hnr_feed_bytes))

    async with httpx.AsyncClient() as client:
        collector = RSSCollector(
            [Feed(source="thehackernews", url=FEED_URL)], client, max_items_per_feed=2
        )
        items = await collector.collect()

    # Берутся первые записи ленты — то есть самые свежие.
    assert len(items) == 2
    assert [item.title for item in items] == [
        "13 Malicious NuGet Packages Turn Fake .NET Job Assessments Into Malware Delivery Traps",
        "Hackers Exploit Two GeoServer Flaws as Researchers Warn of Unauthenticated RCE Chains",
    ]


def test_default_keywords_cover_real_feeds(
    hnr_feed_bytes: bytes,
    bleeping_feed_bytes: bytes,
    php_feed_bytes: bytes,
    node_feed_bytes: bytes,
) -> None:
    # Числа — это фактическое покрытие ключевых слов по умолчанию на снимках.
    # При смене KEYWORDS или обновлении снимков их нужно пересчитать осознанно.
    # php.net даёт 0: там обычные релизы, они попадают в дайджест как доверенная
    # лента, а не по ключевым словам (см. test_select_relevant).
    snapshots = {
        "thehackernews": hnr_feed_bytes,
        "bleepingcomputer": bleeping_feed_bytes,
        "php.net": php_feed_bytes,
        "nodejs": node_feed_bytes,
    }

    matched = {
        source: len(
            filter_items(build_items(source, feedparser.parse(content)), DEFAULT_KEYWORDS).matched
        )
        for source, content in snapshots.items()
    }

    assert matched == {"thehackernews": 37, "bleepingcomputer": 9, "php.net": 0, "nodejs": 64}


def test_trusted_feed_is_relevant_without_security_keywords(php_feed_bytes: bytes) -> None:
    # Релизы php.net не содержат слов про безопасность, но лента доверенная.
    items = build_items("php.net", feedparser.parse(php_feed_bytes), trusted=True)

    relevant, rejected = select_relevant(items, DEFAULT_KEYWORDS)

    assert len(relevant) == 5
    assert rejected == []


def test_untrusted_feed_without_security_keywords_is_dropped(php_feed_bytes: bytes) -> None:
    items = build_items("php.net", feedparser.parse(php_feed_bytes))

    relevant, rejected = select_relevant(items, DEFAULT_KEYWORDS)

    assert relevant == []
    assert len(rejected) == 5


def test_canonical_link_removes_anchor_and_tracking() -> None:
    assert (
        canonical_link("https://Example.com/news/1?utm_source=rss&id=7#top")
        == "https://example.com/news/1?id=7"
    )


def test_canonical_link_keeps_relative_urls() -> None:
    assert canonical_link("/relative/path") == "/relative/path"


def test_clean_summary_removes_tags_and_truncates() -> None:
    assert clean_summary("<p>hello   <b>world</b></p>") == "hello world"
    long_text = clean_summary("x" * 900)
    assert len(long_text) <= 500
    assert long_text.endswith("…")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com", "https://example.com/"),
    ],
)
def test_canonical_link_normalizes_trailing_slash(url: str, expected: str) -> None:
    assert canonical_link(url) == expected


def test_canonical_link_normalizes_scheme_and_www() -> None:
    # Одна и та же статья со ссылками http://www.x/a и https://x/a — это
    # один ключ дедупликации, иначе она попала бы в два тега.
    assert canonical_link("http://www.example.com/news/1") == "https://example.com/news/1"
    assert canonical_link("https://example.com/news/1") == "https://example.com/news/1"


def test_canonical_link_keeps_port() -> None:
    assert canonical_link("https://example.com:8443/a") == "https://example.com:8443/a"


def test_canonical_link_keeps_non_http_scheme() -> None:
    assert canonical_link("ftp://example.com/a") == "ftp://example.com/a"
