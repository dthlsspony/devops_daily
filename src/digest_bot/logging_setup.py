"""Настройка логирования: stdout + ротация файла."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
LOG_FILE_NAME = "digest_bot.log"


def setup_logging(
    level: str = "INFO",
    *,
    log_dir: Path | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Настроить корневой логгер.

    Файловый обработчик добавляется best-effort: если каталог недоступен
    (например, read-only volume), сервис продолжает писать в stdout.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric_level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_dir is None:
        return

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / LOG_FILE_NAME,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError as exc:
        root.warning("log_file_unavailable path=%s error=%s", log_dir, exc)
        return

    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
