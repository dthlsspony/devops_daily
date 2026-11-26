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


def _aware(moment: datetime) -> datetime:
    """Привести время к UTC (наивное время считается UTC)."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


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
