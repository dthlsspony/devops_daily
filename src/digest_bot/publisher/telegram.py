"""Публикация дайджеста в Telegram.

На каждый тег — отдельный пост: заголовок с иконкой и названием тега,
реферат по нему, подвал со счётчиком и источниками.

Сообщения отправляются в ``MarkdownV2``: текст реферата переводится в
безопасный формат (экранирование плюс поддержка подзаголовков, списков и
ссылок), а длинные посты режутся на части по 4096 символов.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

from aiogram.exceptions import TelegramBadRequest

from digest_bot.models import Digest
from digest_bot.tags import DEFAULT_TAG, tag_icon

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
PARSE_MODE_MARKDOWN_V2 = "MarkdownV2"

#: Заголовок раздела в реферате.
_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
#: Пункт списка в реферате.
_BULLET_RE = re.compile(r"^\s*[-*•]\s+\S")
#: Ссылка внутри пункта.
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
#: Фразы-заглушки: раздел с таким текстом нечего печатать.
_PLACEHOLDER_RE = re.compile(
    r"^(нет дополнительной информации|дополнительной информации нет|нет других новостей"
    r"|нет прочих новостей|без дополнительной информации|других новостей нет"
    r"|нет больше новостей|ничего нового нет|нет)\s*[.!]?$",
    re.IGNORECASE,
)

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


def _normalize_bullet(line: str) -> str:
    """Нормализованный текст пункта — для сравнения повторов."""
    text = _URL_RE.sub(" ", line)
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(text.split())


def _first_url(line: str) -> str:
    """Первая ссылка в пункте (без завершающей пунктуации)."""
    match = _URL_RE.search(line)
    if match is None:
        return ""
    return match.group(0).rstrip(".,;:")


def clean_digest(text: str) -> str:
    """Убрать из реферата повторы и разделы без содержания.

    Модель иногда повторяет одну и ту же новость в разделах «Главное» и
    «Детали» (обычно с той же ссылкой) и иногда выводит пустой раздел с
    фразой-заглушкой вроде «Нет дополнительной информации». Повтор ловим по
    ссылке и по совпадению текста, пустые разделы убираем целиком.

    Если после чистки не осталось ни одного пункта, исходный текст
    возвращается как есть: лучше лишний текст, чем пустой пост.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _HEADING_LINE_RE.match(line):
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    blocks.append(current)

    seen_urls: set[str] = set()
    seen_texts: set[str] = set()
    kept: list[list[str]] = []

    for block in blocks:
        heading = block[0] if block and _HEADING_LINE_RE.match(block[0]) else None
        body = block[1:] if heading is not None else block

        kept_body: list[str] = []
        for line in body:
            if _BULLET_RE.match(line):
                url = _first_url(line)
                normalized = _normalize_bullet(line)
                if (url and url in seen_urls) or (normalized and normalized in seen_texts):
                    continue
                if url:
                    seen_urls.add(url)
                if normalized:
                    seen_texts.add(normalized)
            kept_body.append(line)

        if heading is not None:
            has_bullets = any(_BULLET_RE.match(line) for line in kept_body)
            text_body = " ".join(line.strip() for line in kept_body).strip()
            if not has_bullets and (not text_body or _PLACEHOLDER_RE.match(text_body)):
                # Раздел без содержания: не печатаем его вовсе.
                continue

        kept.append([heading, *kept_body] if heading is not None else kept_body)

    if not any(_BULLET_RE.match(line) for block in kept for line in block):
        return text
    return "\n".join("\n".join(block) for block in kept)


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
        """Собрать итоговое сообщение: заголовок с тегом, реферат и хэштег.

        Счётчика новостей и источников нет: он считал переданные модели записи,
        а читатель видит свёрнутый реферат — числа расходились.
        """
        tag = digest.tag or DEFAULT_TAG
        header = f"{tag_icon(tag)} {escape_markdown_v2(tag)}"
        hashtag = escape_markdown_v2(f"#{tag}")
        body = to_markdown_v2(clean_digest(digest.text))
        return f"{header}\n\n{body}\n\n{hashtag}"

    async def _send(self, chunk: str) -> None:
        """Отправить одну часть; если Telegram не разобрал разметку — как plain text."""
        try:
            await self._bot.send_message(self._chat_id, chunk, parse_mode=self._parse_mode)
        except TelegramBadRequest as exc:
            if "parse entities" not in str(exc).lower():
                raise
            logger.warning("telegram_markdown_rejected error=%s", str(exc)[:200])
            await self._bot.send_message(self._chat_id, strip_markdown_v2(chunk), parse_mode=None)
