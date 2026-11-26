"""Тесты чистки реферата: повторы между разделами и пустые разделы."""

from __future__ import annotations

from digest_bot.publisher.telegram import clean_digest

#: Реферат как в примере: раздел «Детали» с фразой-заглушкой.
WITH_PLACEHOLDER = """## Главное
- Первый пункт со ссылкой (https://example.com/a)
- Второй пункт со ссылкой (https://example.com/b)
- Третий пункт со ссылкой (https://example.com/c)

## Детали
Нет дополнительной информации"""

#: Реферат как в примере: те же две ссылки в обоих разделах.
WITH_REPEATS = """## Главное
- Уязвимости в GitHub позволили получить данные 1Password (https://example.com/onepassword)
- Боты находят ключ AWS из образа Docker за восемь секунд (https://example.com/docker)

## Детали
- Подробности инцидента с утечкой через GitHub (https://example.com/onepassword)
- Эксперимент показал скорость реакции инструментов (https://example.com/docker)"""


def test_placeholder_section_is_removed() -> None:
    cleaned = clean_digest(WITH_PLACEHOLDER)

    assert "Детали" not in cleaned
    assert "Нет дополнительной информации" not in cleaned
    assert "Главное" in cleaned
    assert cleaned.count("https://example.com/") == 3


def test_repeated_news_is_removed_from_details() -> None:
    cleaned = clean_digest(WITH_REPEATS)

    assert "Детали" not in cleaned
    assert cleaned.count("https://example.com/onepassword") == 1
    assert cleaned.count("https://example.com/docker") == 1


def test_section_with_unique_news_is_kept() -> None:
    text = (
        "## Главное\n"
        "- Первый пункт (https://example.com/a)\n"
        "\n"
        "## Детали\n"
        "- Другая новость (https://example.com/b)"
    )

    cleaned = clean_digest(text)

    assert "Главное" in cleaned
    assert "Детали" in cleaned
    assert "Другая новость" in cleaned


def test_identical_bullets_are_deduplicated() -> None:
    text = "## Главное\n- Один и тот же пункт\n- Один и тот же пункт"

    cleaned = clean_digest(text)

    assert cleaned.count("Один и тот же пункт") == 1


def test_digest_without_bullets_is_left_as_is() -> None:
    text = "Свежих новостей по безопасности не найдено."

    assert clean_digest(text) == text


def test_prose_section_without_bullets_is_kept() -> None:
    text = "## Итог\nСобытий недели не было."

    cleaned = clean_digest(text)

    assert "Итог" in cleaned
    assert "Событий недели не было." in cleaned


def test_preamble_before_first_heading_is_kept() -> None:
    text = "Коротко о главном.\n\n## Главное\n- Пункт (https://example.com/a)"

    cleaned = clean_digest(text)

    assert "Коротко о главном." in cleaned
    assert "- Пункт" in cleaned


def test_empty_section_body_is_removed() -> None:
    text = "## Главное\n- Пункт (https://example.com/a)\n\n## Детали\n"

    cleaned = clean_digest(text)

    assert "Детали" not in cleaned
    assert "Пункт" in cleaned
