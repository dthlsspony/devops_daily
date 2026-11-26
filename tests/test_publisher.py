"""Тесты публикатора в Telegram."""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from digest_bot.models import Digest
from digest_bot.publisher.telegram import (
    TelegramPublisher,
    split_message,
    strip_markdown_v2,
    to_markdown_v2,
    utf16_length,
)


class FakeBot:
    """Заглушка Telegram-бота: запоминает отправленные сообщения."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str | None]] = []

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str | None = None,
    ) -> Any:
        self.messages.append((str(chat_id), text, parse_mode))
        return None


class RejectingBot(FakeBot):
    """Бот, который отклоняет MarkdownV2 с заданной ошибкой Telegram."""

    def __init__(self, message: str = "can't parse entities") -> None:
        super().__init__()
        self.message = message

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str | None = None,
    ) -> Any:
        if parse_mode is not None:
            raise TelegramBadRequest(SendMessage(chat_id=1, text="x"), self.message)
        return await super().send_message(chat_id, text, parse_mode)


def _digest(text: str, count: int = 1) -> Digest:
    return Digest(text=text, item_count=count, sources=("thehackernews",), tokens=10)


async def test_publish_sends_single_message() -> None:
    bot = FakeBot()
    publisher = TelegramPublisher(bot, "-100123", rate_limit_delay=0)

    await publisher.publish(_digest("## Главное\n- пункт"))

    assert len(bot.messages) == 1
    chat_id, text, parse_mode = bot.messages[0]
    assert chat_id == "-100123"
    assert parse_mode == "MarkdownV2"
    assert "*Главное*" in text
    assert "• пункт" in text


async def test_long_digest_is_split_in_two_messages() -> None:
    bot = FakeBot()
    publisher = TelegramPublisher(bot, "-100123", rate_limit_delay=0)

    await publisher.publish(_digest("a" * 5000))

    assert len(bot.messages) == 2
    assert all(utf16_length(text) <= 4096 for _, text, _ in bot.messages)
    # Заголовок не уезжает отдельным пустым сообщением.
    assert bot.messages[0][1].startswith("🔐")
    assert "a" * 100 in bot.messages[0][1]
    assert bot.messages[1][1].rstrip().endswith("Источников: 1")


async def test_markdown_is_rejected_fallback_to_plain_text() -> None:
    bot = RejectingBot()
    publisher = TelegramPublisher(bot, "-100123", rate_limit_delay=0)

    await publisher.publish(_digest("текст. с точкой"))

    assert len(bot.messages) == 1
    _, text, parse_mode = bot.messages[0]
    assert parse_mode is None
    assert "\\." not in text


async def test_other_telegram_errors_are_not_masked() -> None:
    bot = RejectingBot(message="message is too long")
    publisher = TelegramPublisher(bot, "-100123", rate_limit_delay=0)

    with pytest.raises(TelegramBadRequest):
        await publisher.publish(_digest("текст"))

    assert bot.messages == []


async def test_empty_digest_is_rejected() -> None:
    bot = FakeBot()
    publisher = TelegramPublisher(bot, "-100123", rate_limit_delay=0)

    with pytest.raises(ValueError):
        await publisher.publish(_digest(""))

    assert bot.messages == []


def test_headings_are_converted_to_bold() -> None:
    assert to_markdown_v2("## Заголовок") == "*Заголовок*"


def test_bullets_are_converted() -> None:
    assert to_markdown_v2("- пункт") == "• пункт"


def test_special_characters_are_escaped() -> None:
    converted = to_markdown_v2("Version 1.2.3 (beta)")

    assert converted == "Version 1\\.2\\.3 \\(beta\\)"


def test_inline_markdown_is_preserved() -> None:
    converted = to_markdown_v2("**жирный** и `код`")

    assert converted == "*жирный* и `код`"


def test_links_are_preserved() -> None:
    converted = to_markdown_v2("[источник](https://example.com/a)")

    assert converted == "[источник](https://example.com/a)"


def test_plain_text_link_is_escaped() -> None:
    converted = to_markdown_v2("https://example.com/a")

    assert converted == "https://example\\.com/a"


def test_split_message_respects_limit() -> None:
    chunks = split_message("x" * 100, limit=30)

    assert len(chunks) == 4
    assert all(len(chunk) <= 30 for chunk in chunks)


def test_split_message_keeps_short_text_whole() -> None:
    assert split_message("коротко", limit=100) == ["коротко"]


def test_split_message_prefers_paragraph_boundary() -> None:
    text = "первый абзац\n\nвторой абзац"

    chunks = split_message(text, limit=15)

    assert chunks == ["первый абзац", "второй абзац"]


def test_split_message_does_not_split_escape_pair() -> None:
    text = "a" * 29 + "\\." + "b" * 10

    chunks = split_message(text, limit=30)

    assert chunks == ["a" * 29, "\\." + "b" * 10]


def test_split_message_counts_utf16_units() -> None:
    text = "🔐" * 3000  # 3000 символов, но 6000 UTF-16 кодовых единиц

    chunks = split_message(text, limit=4096)

    assert len(chunks) == 2
    assert all(utf16_length(chunk) <= 4096 for chunk in chunks)


def test_split_message_returns_nothing_for_whitespace() -> None:
    assert split_message("   ") == []
    assert split_message("", limit=2) == []


def test_utf16_length_counts_emoji_as_two_units() -> None:
    assert utf16_length("ab") == 2
    assert utf16_length("🔐") == 2


def test_strip_markdown_v2_returns_readable_text() -> None:
    assert strip_markdown_v2("Version 1\\.2") == "Version 1.2"
    assert strip_markdown_v2("• пункт") == "- пункт"


@pytest.mark.parametrize("limit", [0, -1])
def test_split_message_rejects_invalid_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        split_message("text", limit=limit)
