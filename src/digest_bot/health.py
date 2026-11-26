"""Наблюдаемость: heartbeat-файл и HTTP-эндпоинт ``/healthz``.

Heartbeat обновляется после каждого цикла, поэтому внешний монитор видит не
только «процесс жив», но и «циклы идут и не падают».
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Сколько циклов подряд могут упасть, прежде чем сервис считается нездоровым.
#: Один сбой (например, кратковременная недоступность API) — не повод для тревоги.
MAX_CONSECUTIVE_FAILURES = 3


@dataclass(slots=True)
class Heartbeat:
    """Состояние сервиса на диске: время последнего цикла и счётчик сбоев."""

    path: Path
    failures: int = 0

    def record_start(self) -> None:
        """Отметить запуск сервиса."""
        self.failures = 0
        self._write("starting")

    def record_success(self, **details: Any) -> None:
        """Отметить успешно завершённый цикл."""
        self.failures = 0
        self._write("ok", details)

    def record_failure(self, error: str) -> None:
        """Отметить упавший цикл."""
        self.failures += 1
        self._write("error", {"error": error, "failures": self.failures})

    def _write(self, status: str, details: dict[str, Any] | None = None) -> None:
        payload = {
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
            "details": details or {},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def read(self) -> dict[str, Any]:
        """Прочитать состояние из файла (пустой dict, если файла нет)."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def age_seconds(self) -> float | None:
        """Сколько секунд прошло с последней отметки."""
        stamp = self.read().get("timestamp")
        if not isinstance(stamp, str):
            return None
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return (datetime.now(UTC) - moment).total_seconds()

    def is_healthy(self, max_age_seconds: int) -> bool:
        """Здоров ли сервис: отметка свежая и сбои не идут подряд."""
        details = self.read().get("details")
        failures = details.get("failures", 0) if isinstance(details, dict) else 0
        age = self.age_seconds()
        return (
            age is not None
            and age <= max_age_seconds
            and not (isinstance(failures, int) and failures >= MAX_CONSECUTIVE_FAILURES)
        )


def _make_handler(heartbeat: Heartbeat, max_age_seconds: int) -> type[BaseHTTPRequestHandler]:
    """Собрать обработчик HTTP-запросов health-эндпоинта."""

    class HealthHandler(BaseHTTPRequestHandler):
        server_version = "SecurityDigestBot/1.0"

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0].rstrip("/") not in ("", "/healthz", "/health"):
                self.send_error(404, "not found")
                return
            payload = heartbeat.read()
            details = payload.get("details")
            healthy = heartbeat.is_healthy(max_age_seconds)
            body = json.dumps(
                {
                    "status": "ok" if healthy else "unhealthy",
                    "cycle": payload.get("status", "unknown"),
                    "age_seconds": heartbeat.age_seconds(),
                    "details": details if isinstance(details, dict) else {},
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("health_request " + format, *args)

    return HealthHandler


def start_health_server(
    host: str,
    port: int,
    heartbeat: Heartbeat,
    max_age_seconds: int,
) -> ThreadingHTTPServer:
    """Запустить health-сервер в фоновом потоке."""
    server = ThreadingHTTPServer((host, port), _make_handler(heartbeat, max_age_seconds))
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    logger.info("health_server_started host=%s port=%d", host, port)
    return server
