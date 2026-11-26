"""Публикация дайджеста в Telegram.

Сообщения отправляются в ``MarkdownV2``: текст реферата переводится в
безопасный формат (экранирование плюс поддержка подзаголовков, списков и
ссылок), а длинные дайджесты режутся на части по 4096 символов.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

from aiogram.exceptions import TelegramBadRequest

from digest_bot.models import Digest

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
PARSE_MODE_MARKDOWN_V2 = "MarkdownV2"
HEADER_TEXT = "Дайджест по информационной безопасности"
HEADER_ICON = "🔐"

#: Символы, которые Telegram считает специальными в MarkdownV2.
_SPECIALS_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*\S)\s*$")
_UNESCAPE_RE = re.compile(r"\\([_*\[\]()~`>#+\-=|{}.!])")
_INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<bold>\*\*(?P<bold_text>[^*]+)\*\*)"
    r"|(?P<italic>[*_](?P<italic_text>[^*_]+)[*_])"
    r"|(?P<link>\[(?P<link_text>[^\]]*)\]\((?P<link_url>[^)\s]+)\))"
)


class MessageSender(Protocol):
    """Минимальный контракт Telegram-бота."""

    async def send_message(
        self, chat_id: int | str, text: str, *, parse_mode: str | None = None
    ) -> Any:
        """Отправить сообщение в чат (``parse_mode`` — только по имени)."""
        ...


def escape_markdown_v2(text: str) -> str:
    """Экранировать все специальные символы MarkdownV2."""
    return _SPECIALS_RE.sub(r"\\\1", text)


def _escape_code(text: str) -> str:
    """Экранировать только содержимое code-span."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _escape_url(url: str) -> str:
    """Экранировать URL: Telegram не принимает «сырые» ``)`` и ``\\``."""
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _convert_inline(text: str) -> str:
    """Перевести инлайновую разметку в MarkdownV2."""
    result: list[str] = []
    position = 0
    for match in _INLINE_RE.finditer(text):
        result.append(escape_markdown_v2(text[position : match.start()]))
        if match.group("code") is not None:
            result.append(f"`{_escape_code(match.group('code')[1:-1])}`")
        elif match.group("bold") is not None:
            result.append(f"*{escape_markdown_v2(match.group('bold_text'))}*")
        elif match.group("italic") is not None:
            result.append(f"_{escape_markdown_v2(match.group('italic_text'))}_")
        else:
            label = match.group("link_text") or ""
            url = match.group("link_url") or ""
            result.append(f"[{escape_markdown_v2(label)}]({_escape_url(url)})")
        position = match.end()
    result.append(escape_markdown_v2(text[position:]))
    return "".join(result)


def to_markdown_v2(text: str) -> str:
    """Перевести лёгкий markdown-документ в синтаксис Telegram MarkdownV2."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    converted: list[str] = []
    for line in normalized.split("\n"):
        heading = _HEADING_RE.match(line)
        if heading is not None:
            converted.append(f"*{_convert_inline(heading.group(1))}*")
            continue
        bullet = _BULLET_RE.match(line)
        if bullet is not None:
            converted.append(f"{bullet.group(1)}• {_convert_inline(bullet.group(2))}")
            continue
        converted.append(_convert_inline(line))
    return "\n".join(converted)


def strip_markdown_v2(text: str) -> str:
    """Приблизительно вернуть MarkdownV2 к обычному тексту (fallback)."""
    plain = _UNESCAPE_RE.sub(r"\1", text)
    return plain.replace("•", "-")


def utf16_length(text: str) -> int:
    """Длина строки в UTF-16 кодовых единицах: так Telegram считает лимит."""
    return len(text.encode("utf-16-le")) // 2


def _fit_index(text: str, limit: int) -> int:
    """Максимальный индекс, до которого начало текста укладывается в лимит."""
    low, high = 0, min(len(text), limit)
    while low < high:
        middle = (low + high + 1) // 2
        if utf16_length(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return low


def _find_cut(window: str, limit: int) -> int:
    """Где разрезать окно: абзац → перевод строки → пробел → жёстко.

    Граница используется, только если она не в первой половине окна, иначе
    короткий заголовок уедет отдельным сообщением.
    """
    for separator in ("\n\n", "\n", " "):
        cut = window.rfind(separator)
        if cut >= limit // 2:
            return cut
    cut = len(window)
    if cut > 1 and window[cut - 1] == "\\":
        # Не разрывать пару «\» + экранируемый символ.
        cut -= 1
    return cut


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Разрезать текст на части не длиннее ``limit`` UTF-16 кодовых единиц.

    Разрез выполняется по границам абзацев, затем по переводу строки и пробелу,
    в крайнем случае — жёстко по длине.
    """
    if limit <= 0:
        raise ValueError("limit должен быть положительным")
    chunks: list[str] = []
    remaining = text
    while utf16_length(remaining) > limit:
        window = remaining[: _fit_index(remaining, limit)]
        if not window:
            raise ValueError("limit меньше длины одного символа")
        cut = _find_cut(window, limit)
        chunk = remaining[:cut].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].lstrip()
    if remaining.strip():
        chunks.append(remaining)
    return chunks


class TelegramPublisher:
    """Отправляет дайджест в Telegram-канал."""

    def __init__(
        self,
        bot: MessageSender,
        chat_id: int | str,
        *,
        max_message_length: int = TELEGRAM_MESSAGE_LIMIT,
        rate_limit_delay: float = 1.0,
        parse_mode: str = PARSE_MODE_MARKDOWN_V2,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._max_message_length = max_message_length
        self._rate_limit_delay = rate_limit_delay
        self._parse_mode = parse_mode

    async def publish(self, digest: Digest) -> None:
        """Отправить дайджест, при необходимости — несколькими сообщениями.

        Пустой реферат — ошибка, а не «тихий пропуск»: вызвавший не должен
        помечать новости как отправленные.
        """
        if not digest.text.strip():
            raise ValueError("Пустой дайджест нельзя опубликовать")

        message = self._compose(digest)
        chunks = split_message(message, self._max_message_length)
        logger.info("publish_started parts=%d length=%d", len(chunks), utf16_length(message))
        for index, chunk in enumerate(chunks):
            await self._send(chunk)
            if self._rate_limit_delay > 0 and index < len(chunks) - 1:
                await asyncio.sleep(self._rate_limit_delay)
        logger.info("publish_finished parts=%d", len(chunks))

    def _compose(self, digest: Digest) -> str:
        """Собрать итоговое сообщение из реферата."""
        header = f"{HEADER_ICON} {escape_markdown_v2(HEADER_TEXT)}"
        footer = escape_markdown_v2(
            f"Новостей: {digest.item_count} · Источников: {len(digest.sources)}"
        )
        body = to_markdown_v2(digest.text)
        return f"{header}\n\n{body}\n\n{footer}"

    async def _send(self, chunk: str) -> None:
        """Отправить одну часть; если Telegram не разобрал разметку — как plain text."""
        try:
            await self._bot.send_message(self._chat_id, chunk, parse_mode=self._parse_mode)
        except TelegramBadRequest as exc:
            if "parse entities" not in str(exc).lower():
                raise
            logger.warning("telegram_markdown_rejected error=%s", str(exc)[:200])
            await self._bot.send_message(self._chat_id, strip_markdown_v2(chunk), parse_mode=None)
