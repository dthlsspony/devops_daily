"""Тесты суммаризатора GigaChat."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from digest_bot.models import Digest, NewsItem
from digest_bot.summarizer.gigachat import (
    ChatCompletion,
    EmptyResponseError,
    GigaChatSummarizer,
    SummarizerError,
)
from digest_bot.summarizer.prompts import SYSTEM_PROMPT, build_prompt


class FakeChatClient:
    """Заглушка чат-бэкенда: запоминает запросы и отдаёт заготовленные ответы."""

    def __init__(self, responses: list[str] | None = None, *, tokens: int = 100) -> None:
        self.responses = responses or ["digest"]
        self.tokens = tokens
        self.payloads: list[Mapping[str, Any]] = []
        self.failures = 0

    async def create(self, payload: Mapping[str, Any]) -> ChatCompletion:
        self.payloads.append(payload)
        if self.failures > 0:
            self.failures -= 1
            raise SummarizerError("ResponseError", status_code=503)
        index = min(len(self.payloads) - 1, len(self.responses) - 1)
        return ChatCompletion(content=self.responses[index], total_tokens=self.tokens)


async def _no_sleep(seconds: float) -> None:
    """Мгновенная пауза между попытками."""
    return None


def _items(count: int) -> list[NewsItem]:
    return [
        NewsItem(
            source="thehackernews",
            title=f"News number {index}",
            link=f"https://example.com/{index}",
            summary=f"Summary {index}",
        )
        for index in range(count)
    ]


def test_prompt_contains_all_titles() -> None:
    items = _items(3)

    prompt = build_prompt(items)

    for item in items:
        assert item.title in prompt
        assert item.link in prompt


def test_system_prompt_requests_plain_markdown() -> None:
    assert "##" in SYSTEM_PROMPT
    assert "- пункт" in SYSTEM_PROMPT


async def test_summarize_returns_digest() -> None:
    client = FakeChatClient(["## Главное\n- что-то"], tokens=321)
    summarizer = GigaChatSummarizer(client, "GigaChat-2-Max")

    digest = await summarizer.summarize(_items(2))

    assert isinstance(digest, Digest)
    assert digest.text == "## Главное\n- что-то"
    assert digest.item_count == 2
    assert digest.sources == ("thehackernews",)
    assert digest.tokens == 321


async def test_prompt_is_sent_to_model() -> None:
    client = FakeChatClient()
    summarizer = GigaChatSummarizer(client, "GigaChat-2-Max")

    await summarizer.summarize(_items(1))

    payload = client.payloads[0]
    assert payload["model"] == "GigaChat-2-Max"
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "News number 0" in messages[1]["content"]


async def test_large_batch_is_split() -> None:
    client = FakeChatClient(["часть 1", "часть 2"])
    summarizer = GigaChatSummarizer(client, "GigaChat-2-Max", batch_size=30)

    digest = await summarizer.summarize(_items(35))

    assert len(client.payloads) == 2
    assert digest.text == "часть 1\n\nчасть 2"
    assert digest.item_count == 35
    assert digest.tokens == 200


async def test_empty_items_are_rejected() -> None:
    summarizer = GigaChatSummarizer(FakeChatClient(), "GigaChat-2-Max")

    with pytest.raises(ValueError):
        await summarizer.summarize([])


async def test_request_is_retried() -> None:
    client = FakeChatClient()
    client.failures = 1
    summarizer = GigaChatSummarizer(client, "GigaChat-2-Max", sleep=_no_sleep)

    digest = await summarizer.summarize(_items(1))

    assert len(client.payloads) == 2
    assert digest.text == "digest"


async def test_request_fails_after_all_attempts() -> None:
    client = FakeChatClient()
    client.failures = 5
    summarizer = GigaChatSummarizer(client, "GigaChat-2-Max", sleep=_no_sleep)

    with pytest.raises(RuntimeError):
        await summarizer.summarize(_items(1))

    assert len(client.payloads) == 3


async def test_empty_response_is_retried() -> None:
    client = FakeChatClient(["", "   ", "digest"])
    summarizer = GigaChatSummarizer(client, "GigaChat-2-Max", sleep=_no_sleep)

    digest = await summarizer.summarize(_items(1))

    assert len(client.payloads) == 3
    assert digest.text == "digest"


async def test_persistently_empty_response_fails() -> None:
    client = FakeChatClient([""])
    summarizer = GigaChatSummarizer(client, "GigaChat-2-Max", sleep=_no_sleep)

    with pytest.raises(EmptyResponseError):
        await summarizer.summarize(_items(1))

    assert len(client.payloads) == 3


async def test_auth_error_is_not_retried() -> None:
    class UnauthorizedClient:
        async def create(self, payload: Mapping[str, Any]) -> ChatCompletion:
            raise SummarizerError("AuthenticationError", status_code=401)

    summarizer = GigaChatSummarizer(UnauthorizedClient(), "GigaChat-2-Max", sleep=_no_sleep)

    with pytest.raises(SummarizerError):
        await summarizer.summarize(_items(1))
