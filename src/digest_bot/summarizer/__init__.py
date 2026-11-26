"""Реферирование новостей."""

from __future__ import annotations

from digest_bot.summarizer.base import Summarizer
from digest_bot.summarizer.gigachat import (
    ChatClient,
    ChatCompletion,
    GigaChatClient,
    GigaChatSummarizer,
    SummarizerError,
)

__all__ = [
    "ChatClient",
    "ChatCompletion",
    "GigaChatClient",
    "GigaChatSummarizer",
    "Summarizer",
    "SummarizerError",
]
