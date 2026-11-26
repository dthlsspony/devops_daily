"""Фильтры отбора новостей: по ключевым словам, по свежести и по объёму.

Сопоставление выполняется по ``title + summary`` в нижнем регистре.
Ключевые слова ищутся как «слова», а не как подстроки: ``rce`` не должен
совпадать внутри ``source``. При этом допускаются простые формы
множественного числа (``vulnerabilities``) и цифра сразу после слова
(``php8``, ``npm10``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from digest_bot.models import FilterResult, NewsItem

#: Время для новостей без даты при сортировке «сначала свежие».
_OLDEST = datetime.min.replace(tzinfo=UTC)


def compile_keyword(keyword: str) -> re.Pattern[str]:
    """Собрать регулярное выражение для одного ключевого слова."""
    core = re.escape(keyword.strip().lower())
    if not core:
        # Шаблон, который не совпадает ни с чем.
        return re.compile(r"(?!x)x")
    forms = f"{core}|{core[:-1]}ies" if core.endswith("y") else f"{core}(?:s|es)?"
    return re.compile(rf"(?<![a-z0-9])(?:{forms})(?![a-z])")


def compile_keywords(keywords: Sequence[str]) -> list[re.Pattern[str]]:
    """Собрать регулярные выражения для списка ключевых слов."""
    return [compile_keyword(keyword) for keyword in keywords if keyword.strip()]


def matches_keywords(item: NewsItem, patterns: Sequence[re.Pattern[str]]) -> bool:
    """Проверить, подходит ли новость под хотя бы один из шаблонов."""
    haystack = f"{item.title} {item.summary}".lower()
    return any(pattern.search(haystack) for pattern in patterns)


def filter_items(items: Sequence[NewsItem], keywords: Sequence[str]) -> FilterResult:
    """Разделить новости на подходящие и неподходящие."""
    patterns = compile_keywords(keywords)
    matched: list[NewsItem] = []
    rejected: list[NewsItem] = []
    for item in items:
        if matches_keywords(item, patterns):
            matched.append(item)
        else:
            rejected.append(item)
    return FilterResult(matched=tuple(matched), rejected=tuple(rejected))


def select_relevant(
    items: Sequence[NewsItem], keywords: Sequence[str]
) -> tuple[list[NewsItem], list[NewsItem]]:
    """Отобрать релевантные новости и отбросить остальные.

    Релевантна новость, в которой есть ключевое слово **или** которая пришла
    из доверенной ленты (официальные advisory и релизы — их тема известна
    заранее, даже если в заголовке нет слов про безопасность).
    """
    patterns = compile_keywords(keywords)
    relevant: list[NewsItem] = []
    rejected: list[NewsItem] = []
    for item in items:
        if item.trusted or matches_keywords(item, patterns):
            relevant.append(item)
        else:
            rejected.append(item)
    return relevant, rejected


def to_utc(moment: datetime) -> datetime:
    """Привести время к UTC (наивное время считается UTC)."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _aware(moment: datetime) -> datetime:
    """Привести время к UTC (наивное время считается UTC)."""
    return to_utc(moment)


def recent_items(
    items: Sequence[NewsItem],
    max_age: timedelta | None,
    now: datetime | None = None,
) -> list[NewsItem]:
    """Оставить новости не старше ``max_age``.

    Новости без даты не отбрасываются: по ним свежесть неизвестна. ``max_age=None``
    отключает фильтр.
    """
    if max_age is None:
        return list(items)
    moment = _aware(now) if now is not None else datetime.now(UTC)
    threshold = moment - max_age
    return [
        item
        for item in items
        if item.published_at is None or _aware(item.published_at) >= threshold
    ]


def newest_items(items: Sequence[NewsItem], limit: int | None) -> list[NewsItem]:
    """Оставить самые свежие ``limit`` новостей (без даты — в конце)."""
    if limit is None:
        return list(items)
    ordered = sorted(items, key=lambda item: _aware(item.published_at or _OLDEST), reverse=True)
    return ordered[:limit]


#: Слова, которые ничего не различают в заголовках новостей.
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "after",
        "its",
        "it",
        "new",
        "now",
        "how",
        "why",
        "what",
        "who",
        "amid",
        "via",
        "that",
        "this",
        "but",
        "not",
        "you",
        "your",
    }
)

#: Насколько похожие заголовки считать одним сюжетом (коэффициент Жаккара).
DUPLICATE_SIMILARITY = 0.6


def _title_tokens(title: str) -> set[str]:
    """Значимые слова заголовка."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {word for word in words if word not in _STOP_WORDS and len(word) > 2}


def _similarity(first: set[str], second: set[str]) -> float:
    """Коэффициент Жаккара двух множеств слов."""
    union = first | second
    if not union:
        return 0.0
    return len(first & second) / len(union)


def cluster_duplicates(
    items: Sequence[NewsItem], threshold: float = DUPLICATE_SIMILARITY
) -> tuple[list[NewsItem], dict[str, list[NewsItem]]]:
    """Свернуть новости об одном сюжете, оставленных разными изданиями.

    Один сюжет часто выходит в двух лентах (разные ссылки, разные теги) — без
    свёртки он публикуется дважды, и читатель видит одну новость в двух
    постах. Свертываются **только новости из разных источников**: внутри одной
    ленты похожие заголовки обычно означают разные новости (ежедневные сводки
    CISA, advisory по разным вендорам).

    Возвращает оставшиеся новости и отображение «оставленная → свёрнутые».
    """
    ordered = sorted(items, key=lambda item: _aware(item.published_at or _OLDEST), reverse=True)
    kept: list[NewsItem] = []
    kept_tokens: list[set[str]] = []
    duplicates: dict[str, list[NewsItem]] = {}

    for item in ordered:
        tokens = _title_tokens(item.title)
        duplicate_of = ""
        if len(tokens) >= 3:
            for index, other in enumerate(kept):
                if other.source == item.source:
                    continue
                if _similarity(tokens, kept_tokens[index]) >= threshold:
                    duplicate_of = other.link
                    break
        if duplicate_of:
            duplicates.setdefault(duplicate_of, []).append(item)
            continue
        kept.append(item)
        kept_tokens.append(tokens)

    return kept, duplicates
