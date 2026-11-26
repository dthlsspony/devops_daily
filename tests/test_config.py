"""Тесты конфигурации."""

from __future__ import annotations

import pytest
from pydantic_settings import SettingsConfigDict

from digest_bot.config import (
    DEFAULT_FEEDS,
    DEFAULT_KEYWORDS,
    HEARTBEAT_GRACE_SECONDS,
    Settings,
)


class EnvSettings(Settings):
    """Настройки только из окружения: файл ``.env`` не читается."""

    model_config = SettingsConfigDict(env_file=None)


#: Переменные, которые влияют на проверяемые значения по умолчанию.
_MANAGED_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHANNEL_ID",
    "GIGACHAT_CREDENTIALS",
    "MIN_NEWS_THRESHOLD",
    "SCHEDULE_HOURS",
    "HEARTBEAT_MAX_AGE_SECONDS",
    "FEEDS",
    "KEYWORDS",
    "MAX_ITEMS_PER_FEED",
    "MAX_ITEM_AGE_DAYS",
    "MAX_ITEMS_PER_DIGEST",
    "GIGACHAT_VERIFY_SSL",
)


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key in _MANAGED_VARS:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _settings() -> Settings:
    """Настройки только из окружения: файл ``.env`` игнорируется."""
    return EnvSettings()


def test_defaults_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)

    settings = _settings()

    assert settings.min_news_threshold == 2
    assert settings.schedule_hours == 4
    assert settings.gigachat_model == "GigaChat-2-Max"
    assert settings.db_path.as_posix() == "data/seen.sqlite"
    assert settings.feeds == list(DEFAULT_FEEDS)
    assert settings.keywords == list(DEFAULT_KEYWORDS)


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHANNEL_ID="-100",
        GIGACHAT_CREDENTIALS="creds",
        MIN_NEWS_THRESHOLD="5",
        SCHEDULE_HOURS="6",
    )

    settings = _settings()

    assert settings.telegram_bot_token == "token"
    assert settings.telegram_channel_id == "-100"
    assert settings.gigachat_credentials == "creds"
    assert settings.min_news_threshold == 5
    assert settings.schedule_hours == 6


def test_missing_credentials_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_CHANNEL_ID="",
        GIGACHAT_CREDENTIALS="  ",
    )

    settings = _settings()

    assert set(settings.missing_credentials()) == {
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHANNEL_ID",
        "GIGACHAT_CREDENTIALS",
    }


def test_credentials_pass_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHANNEL_ID="-100",
        GIGACHAT_CREDENTIALS="creds",
    )

    settings = _settings()

    assert settings.missing_credentials() == []


def test_feeds_can_be_overridden_with_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, FEEDS='[{"source": "custom", "url": "https://example.com/feed.xml"}]')

    settings = _settings()

    assert len(settings.feeds) == 1
    assert settings.feeds[0].source == "custom"


def test_keywords_can_be_overridden_with_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, KEYWORDS='["php", "cve"]')

    settings = _settings()

    assert settings.keywords == ["php", "cve"]


def test_heartbeat_max_age_follows_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SCHEDULE_HOURS="4")

    # Между циклами heartbeat не обновляется: лимит обязан быть шире интервала.
    assert _settings().heartbeat_max_age == 4 * 3600 + HEARTBEAT_GRACE_SECONDS


def test_heartbeat_max_age_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SCHEDULE_HOURS="4", HEARTBEAT_MAX_AGE_SECONDS="900")

    assert _settings().heartbeat_max_age == 900


def test_default_feeds_are_reachable_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)

    urls = {feed.source: feed.url for feed in _settings().feeds}

    # nginx.org не публикует RSS, а общий блог Node.js отдаёт архив на 1000+ записей.
    assert not any("nginx.org" in url for url in urls.values())
    assert urls["nodejs"] == "https://nodejs.org/en/feed/vulnerability.xml"
    assert all(url.startswith("https://") for url in urls.values())


def test_volume_limits_have_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)

    settings = _settings()

    assert settings.max_items_per_feed == 50
    assert settings.max_item_age_days == 3
    assert settings.max_items_per_digest == 30


def test_zero_disables_volume_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, MAX_ITEM_AGE_DAYS="0", MAX_ITEMS_PER_DIGEST="0")

    settings = _settings()

    assert settings.max_item_age_days is None
    assert settings.max_items_per_digest is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("false", False), ("1", True), ("0", False)],
)
def test_gigachat_verify_ssl_is_parsed(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    _env(monkeypatch, GIGACHAT_VERIFY_SSL=value)

    assert _settings().gigachat_verify_ssl is expected


def test_tls_verification_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)

    settings = _settings()

    assert settings.gigachat_verify_ssl is True
    assert settings.ca_bundle_path.as_posix() == "data/ca-bundle.pem"
