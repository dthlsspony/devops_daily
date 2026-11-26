"""Тесты адаптера над официальным SDK GigaChat: сеть замокана через respx."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import respx

from digest_bot.models import NewsItem
from digest_bot.summarizer.gigachat import GigaChatClient, GigaChatSummarizer, SummarizerError

AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
# SDK переписывает базовый путь /v1 в /v2 для основного контракта chat/completions.
CHAT_URL = "https://api.giga.chat/v2/chat/completions"
MODEL = "GigaChat-2-Max"
CREDENTIALS = base64.b64encode(b"test-key:test-secret").decode()
TOKEN_RESPONSE = {"access_token": "test-token", "expires_at": 4102444800000}
PAYLOAD: dict[str, Any] = {"model": MODEL, "messages": [{"role": "user", "content": "привет"}]}


def _chat_response(content: str) -> dict[str, Any]:
    """Тело ответа в формате контракта chat/completions SDK."""
    return {
        "model": MODEL,
        "created_at": 1700000000,
        "messages": [{"role": "assistant", "content": content}],
        "finish_reason": "stop",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


def _text(content: Any) -> str:
    """Текст сообщения: SDK нормализует строку в список частей."""
    if isinstance(content, str):
        return content
    return "".join(part["text"] for part in content)


def _mock_auth() -> None:
    respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))


async def _no_sleep(seconds: float) -> None:
    """Мгновенная пауза между попытками."""
    return None


def _items(count: int) -> list[NewsItem]:
    return [
        NewsItem(
            source="thehackernews",
            title=f"Critical RCE number {index}",
            link=f"https://example.com/{index}",
            summary="exploit details",
        )
        for index in range(count)
    ]


@respx.mock
async def test_client_authenticates_and_parses_response() -> None:
    _mock_auth()
    chat_route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json=_chat_response("## Главное\n- пункт"))
    )
    client = GigaChatClient(CREDENTIALS, MODEL)
    try:
        completion = await client.create(PAYLOAD)
    finally:
        await client.aclose()

    assert completion.content == "## Главное\n- пункт"
    assert completion.total_tokens == 15
    request = chat_route.calls.last.request
    assert request.headers["authorization"] == "Bearer test-token"
    assert json.loads(request.content)["model"] == MODEL


@respx.mock
async def test_summarizer_sends_all_titles_through_sdk() -> None:
    _mock_auth()
    chat_route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json=_chat_response("реферат"))
    )
    items = _items(3)
    client = GigaChatClient(CREDENTIALS, MODEL)
    try:
        digest = await GigaChatSummarizer(client, MODEL, sleep=_no_sleep).summarize(items)
    finally:
        await client.aclose()

    assert digest.text == "реферат"
    assert digest.item_count == 3
    assert digest.tokens == 15
    sent = json.loads(chat_route.calls.last.request.content)
    messages = sent["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user_prompt = _text(messages[1]["content"])
    for item in items:
        assert item.title in user_prompt
        assert item.link in user_prompt


@respx.mock
async def test_api_error_does_not_leak_response_body() -> None:
    _mock_auth()
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(401, json={"message": "secret body must not leak"})
    )
    client = GigaChatClient(CREDENTIALS, MODEL)
    try:
        with pytest.raises(SummarizerError) as info:
            await client.create(PAYLOAD)
    finally:
        await client.aclose()

    error = info.value
    assert error.status_code == 401
    assert error.retryable is False
    assert "AuthenticationError" in str(error)
    assert "secret body" not in str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


@respx.mock
async def test_auth_failure_is_reported_as_summarizer_error() -> None:
    respx.post(AUTH_URL).mock(return_value=httpx.Response(401, json={"message": "invalid key"}))
    client = GigaChatClient(CREDENTIALS, MODEL)
    try:
        with pytest.raises(SummarizerError) as info:
            await client.create(PAYLOAD)
    finally:
        await client.aclose()

    assert info.value.status_code == 401


@respx.mock
async def test_non_json_success_response_is_reported_as_summarizer_error() -> None:
    _mock_auth()
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, text="<html>proxy</html>"))
    client = GigaChatClient(CREDENTIALS, MODEL)
    try:
        with pytest.raises(SummarizerError) as info:
            await client.create(PAYLOAD)
    finally:
        await client.aclose()

    assert info.value.retryable is True


@respx.mock
async def test_non_retryable_error_is_not_retried() -> None:
    _mock_auth()
    chat_route = respx.post(CHAT_URL).mock(return_value=httpx.Response(401, json={"message": "no"}))
    client = GigaChatClient(CREDENTIALS, MODEL)
    try:
        summarizer = GigaChatSummarizer(client, MODEL, sleep=_no_sleep)
        with pytest.raises(SummarizerError):
            await summarizer.summarize(_items(1))
    finally:
        await client.aclose()

    assert chat_route.call_count == 1


@respx.mock
async def test_server_error_is_retried() -> None:
    _mock_auth()
    chat_route = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.Response(503, json={"message": "unavailable"}),
            httpx.Response(200, json=_chat_response("реферат")),
        ]
    )
    client = GigaChatClient(CREDENTIALS, MODEL)
    try:
        digest = await GigaChatSummarizer(client, MODEL, sleep=_no_sleep).summarize(_items(1))
    finally:
        await client.aclose()

    assert chat_route.call_count == 2
    assert digest.text == "реферат"


@respx.mock
async def test_network_error_is_retried() -> None:
    _mock_auth()
    chat_route = respx.post(CHAT_URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=_chat_response("ок"))]
    )
    client = GigaChatClient(CREDENTIALS, MODEL)
    try:
        digest = await GigaChatSummarizer(client, MODEL, sleep=_no_sleep).summarize(_items(1))
    finally:
        await client.aclose()

    assert chat_route.call_count == 2
    assert digest.text == "ок"
