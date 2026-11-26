"""Общие фикстуры для тестов."""

from __future__ import annotations

from pathlib import Path

import pytest

from digest_bot.models import NewsItem

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixtures_dir() -> Path:
    """Каталог с реальными снимками лент (см. ``fixtures/README.md``)."""
    return FIXTURES_DIR


@pytest.fixture()
def hnr_feed_bytes(fixtures_dir: Path) -> bytes:
    """Снимок ленты The Hacker News (RSS 2.0, 50 записей)."""
    return (fixtures_dir / "hnr_feed.xml").read_bytes()


@pytest.fixture()
def php_feed_bytes(fixtures_dir: Path) -> bytes:
    """Снимок ленты релизов php.net (Atom 1.0, 5 записей)."""
    return (fixtures_dir / "php_net_feed.xml").read_bytes()


@pytest.fixture()
def node_feed_bytes(fixtures_dir: Path) -> bytes:
    """Снимок фида security-advisories Node.js (RSS 2.0 без описаний, 78 записей)."""
    return (fixtures_dir / "node_feed.xml").read_bytes()


@pytest.fixture()
def bleeping_feed_bytes(fixtures_dir: Path) -> bytes:
    """Снимок ленты BleepingComputer (RSS 2.0, 15 записей)."""
    return (fixtures_dir / "bleepingcomputer_feed.xml").read_bytes()


@pytest.fixture()
def sample_item() -> NewsItem:
    """Новость, подходящая под ключевые слова."""
    return NewsItem(
        source="thehackernews",
        title="Critical RCE Vulnerability in Linux Package",
        link="https://example.com/critical-rce",
        summary="A remote code execution flaw was found in a widely used package.",
        published_at=None,
    )
