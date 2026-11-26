"""Суммаризатор на базе официального SDK GigaChat.

SDK используется в асинхронном режиме (``client.achat``), поэтому блокирующих
вызовов в event loop нет. Временные сбои API повторяются с экспоненциальной
задержкой (``tenacity``), а тело ответа в лог не попадает.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from digest_bot.models import Digest, NewsItem
from digest_bot.summarizer.prompts import SYSTEM_PROMPT, build_prompt

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 30
MAX_ATTEMPTS = 3

#: HTTP-статусы, которые имеют смысл повторять.
RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

#: Функция паузы между попытками (в тестах подменяется на мгновенную).
SleepFunc = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    """Нормализованный ответ чат-модели."""

    content: str
    total_tokens: int | None = None


class SummarizerError(RuntimeError):
    """Сбой API GigaChat: только причина и статус, без тела ответа."""

    def __init__(self, reason: str, status_code: int | None = None) -> None:
        detail = f"{reason} status={status_code}" if status_code is not None else reason
        super().__init__(detail)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        """Стоит ли повторять запрос после этой ошибки."""
        return self.status_code is None or self.status_code in RETRYABLE_STATUS_CODES


class EmptyResponseError(RuntimeError):
    """Модель вернула пустой ответ."""


class ChatClient(Protocol):
    """Минимальный контракт чат-бэкенда.

    Позволяет подменить GigaChat на другой OpenAI-совместимый клиент или
    на мок в тестах.
    """

    async def create(self, payload: Mapping[str, Any]) -> ChatCompletion:
        """Выполнить запрос к модели и вернуть ответ."""
        ...


def _is_retryable(exc: BaseException) -> bool:
    """Повторять ли запрос после ошибки (сетевые сбои, 429, 5xx, пустой ответ)."""
    if isinstance(exc, (EmptyResponseError, httpx.HTTPError)):
        return True
    return isinstance(exc, SummarizerError) and exc.retryable


def _log_retry(state: RetryCallState) -> None:
    """Сообщить о повторной попытке, не раскрывая тело ответа."""
    error = "unknown"
    outcome = state.outcome
    if outcome is not None and outcome.failed:
        exception = outcome.exception()
        if exception is not None:
            error = type(exception).__name__
    logger.warning("gigachat_retry attempt=%d error=%s", state.attempt_number, error)


def _extract_content(response: Any) -> str:
    """Достать текст ответа: строка либо список частей сообщения."""
    messages = getattr(response, "messages", None) or []
    if not messages:
        return ""
    content = getattr(messages[0], "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if text:
                parts.append(str(text))
        return "".join(parts)
    return str(content or "")


def _extract_tokens(response: Any) -> int | None:
    """Достать счётчик токенов из ответа (в контракте SDK — ``total_tokens``)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    total = getattr(usage, "total_tokens", None)
    if total is None and isinstance(usage, dict):
        total = usage.get("total_tokens")
    return int(total) if total is not None else None


class GigaChatSummarizer:
    """Строит реферат через GigaChat, разбивая новости на батчи."""

    def __init__(
        self,
        client: ChatClient,
        model: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_attempts: int = MAX_ATTEMPTS,
        sleep: SleepFunc = asyncio.sleep,
    ) -> None:
        self._client = client
        self._model = model
        self._batch_size = max(1, batch_size)
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep

    async def summarize(self, items: list[NewsItem]) -> Digest:
        """Собрать единый реферат по всем новостям.

        При сбое или пустом ответе модели бросает исключение: вызвавший не
        должен помечать новости как обработанные.
        """
        if not items:
            raise ValueError("Нет новостей для реферирования")

        texts: list[str] = []
        tokens = 0
        for batch in self._batches(items):
            completion = await self._ask(batch)
            tokens += completion.total_tokens or 0
            texts.append(completion.content.strip())

        sources = tuple(sorted({item.source for item in items}))
        return Digest(
            text="\n\n".join(texts),
            item_count=len(items),
            sources=sources,
            tokens=tokens,
            tag=items[0].tag if items else "",
        )

    def _batches(self, items: Sequence[NewsItem]) -> list[list[NewsItem]]:
        """Разбить новости на батчи ограниченного размера."""
        return [
            list(items[start : start + self._batch_size])
            for start in range(0, len(items), self._batch_size)
        ]

    async def _ask(self, items: list[NewsItem]) -> ChatCompletion:
        """Один вызов модели с повторными попытками."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(items)},
            ],
        }

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception(_is_retryable),
            before_sleep=_log_retry,
            sleep=self._sleep,
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                completion = await self._client.create(payload)
                if not completion.content.strip():
                    raise EmptyResponseError
                logger.info(
                    "gigachat_request model=%s items=%d tokens=%s",
                    self._model,
                    len(items),
                    completion.total_tokens,
                )
                return completion
        raise RuntimeError("GigaChat не ответил")


class GigaChatClient:
    """Адаптер поверх официального SDK GigaChat.

    ``ca_bundle_file`` — единый CA-bundle (стандартные корни + Минцифры), без
    него проверка TLS к OAuth-серверу GigaChat не проходит.
    """

    def __init__(
        self,
        credentials: str,
        model: str,
        *,
        timeout: float = 30.0,
        verify_ssl_certs: bool = True,
        ca_bundle_file: Path | None = None,
    ) -> None:
        from gigachat import GigaChat

        self._client = GigaChat(
            credentials=credentials,
            model=model,
            timeout=timeout,
            verify_ssl_certs=verify_ssl_certs,
            ca_bundle_file=str(ca_bundle_file) if ca_bundle_file is not None else None,
        )

    async def create(self, payload: Mapping[str, Any]) -> ChatCompletion:
        """Выполнить запрос к GigaChat."""
        from gigachat.exceptions import GigaChatException

        try:
            response = await self._client.achat.create(dict(payload))
        except (GigaChatException, ValueError) as exc:
            # Сообщение исключения SDK содержит тело и заголовки ответа —
            # в логи попадает только тип ошибки и статус. ValueError сюда
            # попадает, если ответ не разобрался (не JSON или другая схема).
            status_code = getattr(exc, "status_code", None)
            raise SummarizerError(type(exc).__name__, status_code) from None
        return ChatCompletion(
            content=_extract_content(response),
            total_tokens=_extract_tokens(response),
        )

    async def aclose(self) -> None:
        """Закрыть HTTP-соединения SDK."""
        await self._client.aclose()
