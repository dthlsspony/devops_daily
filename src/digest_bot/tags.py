"""Тематические теги: классификация новостей и группировка по тегам.

Каждая новость получает **ровно один** тег, поэтому между постами нет повторов.
Тег определяется по заголовку (тема новости) — описание используется только
для отбора релевантности, но не для классификации: иначе случайное слово
в описании («patch», «flaw») уводило новость не в тот тег.

Правило срабатывает, если совпало хотя бы одно условие:

- в заголовке найдено ключевое слово правила;
- источник новости указан в правиле (тег-фолбэк для лент, заголовки которых
  не содержат имени стека — например «Wednesday, November 11, 2026 Security
  Releases» у Node.js).

Порядок правил = приоритет: сначала отличительный тип события (supply chain,
malware, утечки), затем стек, затем общий тип события. Поэтому новость про
вредоносный npm-пакет попадёт в ``supply-chain``, а не в ``nodejs``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from digest_bot.filters import to_utc
from digest_bot.models import NewsItem

#: Тег для новостей, под которые не подошло ни одно правило.
DEFAULT_TAG = "прочее"

#: Самое раннее время — для сортировки новостей без даты.
OLDEST = datetime.min.replace(tzinfo=UTC)

#: Иконка для каждого тега — попадает в заголовок поста.
TAG_ICONS: dict[str, str] = {
    "php": "🐘",
    "nodejs": "🟢",
    "linux": "🐧",
    "nginx": "⚡",
    "web": "🌐",
    "cloud": "☁️",
    "windows": "🪟",
    "apple": "🍎",
    "google": "🔍",
    "devops": "🚢",
    "python": "🐍",
    "java": "☕",
    "ruby": "💎",
    "supply-chain": "📦",
    "malware": "☠️",
    "leaks": "💧",
    "exploits": "💥",
    "advisories": "📋",
    "policy": "⚖️",
    "research": "🔬",
    DEFAULT_TAG: "📰",
}


def tag_icon(tag: str) -> str:
    """Иконка тега (для неизвестного — иконка по умолчанию)."""
    return TAG_ICONS.get(tag, TAG_ICONS[DEFAULT_TAG])


class TagRuleLike(Protocol):
    """Минимальный контракт правила тегирования.

    Реализуется ``TagRule`` из :mod:`digest_bot.config`.
    """

    tag: str
    keywords: list[str]
    sources: list[str]


@dataclass(frozen=True, slots=True)
class CompiledRule:
    """Правило, готовое к применению."""

    tag: str
    patterns: tuple[re.Pattern[str], ...]
    sources: frozenset[str]

    def matches(self, item: NewsItem, haystack: str) -> bool:
        """Подходит ли правило к новости."""
        if item.source in self.sources:
            return True
        return any(pattern.search(haystack) for pattern in self.patterns)


def compile_rules(rules: Sequence[TagRuleLike]) -> list[CompiledRule]:
    """Скомпилировать правила тегирования."""
    from digest_bot.filters import compile_keyword

    compiled: list[CompiledRule] = []
    for rule in rules:
        compiled.append(
            CompiledRule(
                tag=rule.tag,
                patterns=tuple(compile_keyword(keyword) for keyword in rule.keywords),
                sources=frozenset(rule.sources),
            )
        )
    return compiled


def classify(item: NewsItem, rules: Sequence[CompiledRule]) -> str:
    """Определить тег новости: первое подошедшее правило, иначе ``прочее``."""
    haystack = item.title.lower()
    for rule in rules:
        if rule.matches(item, haystack):
            return rule.tag
    return DEFAULT_TAG


def group_by_tag(
    items: Sequence[NewsItem], rules: Sequence[CompiledRule]
) -> list[tuple[str, list[NewsItem]]]:
    """Сгруппировать новости по тегам.

    Порядок групп: сначала тематические теги — от большей группы к меньшей,
    при равенстве — по алфавиту; тег ``прочее`` всегда в конце, иначе общая
    подборка вытесняет более специфичные теги (PHP, Node.js) из постов.
    Внутри группы новости отсортированы от новых к старым.
    """
    groups: dict[str, list[NewsItem]] = {}
    for item in items:
        groups.setdefault(classify(item, rules), []).append(item)

    ordered = sorted(
        groups.items(),
        key=lambda pair: (pair[0] == DEFAULT_TAG, -len(pair[1]), pair[0]),
    )
    result: list[tuple[str, list[NewsItem]]] = []
    for tag, tagged in ordered:
        tagged.sort(key=lambda item: to_utc(item.published_at or OLDEST), reverse=True)
        result.append((tag, tagged))
    return result
