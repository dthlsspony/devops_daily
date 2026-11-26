"""Тесты CLI и служебных модулей: heartbeat и health-эндпоинт."""

from __future__ import annotations

import json
import logging
import signal
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from digest_bot import main as main_module
from digest_bot.config import Settings
from digest_bot.health import MAX_CONSECUTIVE_FAILURES, Heartbeat, start_health_server
from digest_bot.main import build_parser


def test_parser_accepts_run_command() -> None:
    args = build_parser().parse_args(["run"])

    assert args.command == "run"
    assert args.once is False
    assert args.dry_run is False


def test_parser_accepts_single_run() -> None:
    args = build_parser().parse_args(["run", "--once"])

    assert args.once is True


def test_parser_accepts_dry_run() -> None:
    args = build_parser().parse_args(["run", "--dry-run"])

    assert args.dry_run is True


def _heartbeat(tmp_path: Path) -> Heartbeat:
    return Heartbeat(tmp_path / "heartbeat.json")


def _get(url: str) -> tuple[int, Any]:
    """Выполнить GET к локальному health-серверу: статус и тело (JSON или текст)."""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body.decode("utf-8", errors="replace")


def test_heartbeat_records_success(tmp_path: Path) -> None:
    heartbeat = _heartbeat(tmp_path)

    heartbeat.record_success(items=3, sent=True)

    payload = json.loads(heartbeat.path.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["details"] == {"items": 3, "sent": True}
    assert heartbeat.is_healthy(60) is True


def test_missing_heartbeat_is_unhealthy(tmp_path: Path) -> None:
    heartbeat = Heartbeat(tmp_path / "missing.json")

    assert heartbeat.read() == {}
    assert heartbeat.age_seconds() is None
    assert heartbeat.is_healthy(60) is False


def test_single_failure_is_still_healthy(tmp_path: Path) -> None:
    heartbeat = _heartbeat(tmp_path)

    heartbeat.record_failure(error="ValueError")

    assert heartbeat.failures == 1
    assert heartbeat.is_healthy(60) is True


def test_repeated_failures_make_service_unhealthy(tmp_path: Path) -> None:
    heartbeat = _heartbeat(tmp_path)

    for _ in range(MAX_CONSECUTIVE_FAILURES):
        heartbeat.record_failure(error="ValueError")

    assert heartbeat.is_healthy(60) is False


def test_success_resets_failure_counter(tmp_path: Path) -> None:
    heartbeat = _heartbeat(tmp_path)
    heartbeat.record_failure(error="ValueError")

    heartbeat.record_success()

    assert heartbeat.failures == 0
    assert heartbeat.is_healthy(60) is True


def test_stale_heartbeat_is_unhealthy(tmp_path: Path) -> None:
    heartbeat = _heartbeat(tmp_path)
    heartbeat.path.write_text(
        json.dumps({"status": "ok", "timestamp": "2020-01-01T00:00:00+00:00", "details": {}}),
        encoding="utf-8",
    )

    assert heartbeat.is_healthy(60) is False


def test_health_endpoint_reports_ok(tmp_path: Path) -> None:
    heartbeat = _heartbeat(tmp_path)
    heartbeat.record_success(items=2)
    server = start_health_server("127.0.0.1", 0, heartbeat, max_age_seconds=60)
    port = server.server_address[1]

    try:
        status, payload = _get(f"http://127.0.0.1:{port}/healthz")
    finally:
        server.shutdown()

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["cycle"] == "ok"
    assert payload["details"] == {"items": 2}


def test_health_endpoint_reports_unhealthy_service(tmp_path: Path) -> None:
    heartbeat = _heartbeat(tmp_path)
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        heartbeat.record_failure(error="ValueError")
    server = start_health_server("127.0.0.1", 0, heartbeat, max_age_seconds=60)
    port = server.server_address[1]

    try:
        status, payload = _get(f"http://127.0.0.1:{port}/healthz")
    finally:
        server.shutdown()

    assert status == 503
    assert payload["status"] == "unhealthy"
    assert payload["cycle"] == "error"


def test_health_endpoint_answers_404_for_unknown_path(tmp_path: Path) -> None:
    heartbeat = _heartbeat(tmp_path)
    heartbeat.record_success()
    server = start_health_server("127.0.0.1", 0, heartbeat, max_age_seconds=60)
    port = server.server_address[1]

    try:
        status, _ = _get(f"http://127.0.0.1:{port}/metrics")
    finally:
        server.shutdown()

    assert status == 404


class FakeScheduler:
    """Заглушка BlockingScheduler.

    ``SystemExit`` моделирует выход из уже работающего цикла (SIGTERM), любая
    другая ошибка — сбой до запуска: планировщик остаётся остановленным.
    """

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.started = False
        self.running = False
        self.shutdown_calls: list[bool] = []
        self.job_kwargs: dict[str, Any] = {}

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.job_kwargs = kwargs

    def start(self) -> None:
        self.started = True
        self.running = not isinstance(self.error, Exception) or isinstance(self.error, SystemExit)
        if self.error is not None:
            raise self.error

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)
        self.running = False


class FakeHealthServer:
    """Заглушка health-сервера."""

    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _scheduler_settings(tmp_path: Path) -> Settings:
    return Settings(
        heartbeat_path=tmp_path / "heartbeat.json",
        health_host="127.0.0.1",
        health_port=0,
    )


def _patch_scheduler(
    monkeypatch: pytest.MonkeyPatch, fake: FakeScheduler, server: FakeHealthServer
) -> None:
    monkeypatch.setattr(main_module, "BlockingScheduler", lambda **kwargs: fake)
    monkeypatch.setattr(main_module, "start_health_server", lambda *args: server)
    # Не трогаем настоящие обработчики сигналов тестового процесса.
    monkeypatch.setattr(signal, "signal", lambda *args: None)


def test_scheduler_is_stopped_on_shutdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # BlockingScheduler крутит цикл в главном потоке: SystemExit из обработчика
    # SIGTERM выходит из start(), и без явного shutdown процесс не завершается.
    fake = FakeScheduler(error=SystemExit(0))
    server = FakeHealthServer()
    _patch_scheduler(monkeypatch, fake, server)

    main_module.run_scheduler(_scheduler_settings(tmp_path))

    assert fake.started is True
    assert fake.shutdown_calls == [True]
    assert server.shutdown_calls == 1


def test_scheduler_is_not_shut_down_if_it_never_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeScheduler(error=RuntimeError("port in use"))
    server = FakeHealthServer()
    _patch_scheduler(monkeypatch, fake, server)

    with pytest.raises(RuntimeError):
        main_module.run_scheduler(_scheduler_settings(tmp_path))

    assert fake.shutdown_calls == []
    assert server.shutdown_calls == 1


def test_first_cycle_runs_immediately(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeScheduler(error=SystemExit(0))
    _patch_scheduler(monkeypatch, fake, FakeHealthServer())
    settings = _scheduler_settings(tmp_path)
    settings.schedule_hours = 4

    main_module.run_scheduler(settings)

    trigger = fake.job_kwargs["trigger"]
    assert fake.job_kwargs["next_run_time"] is not None
    assert str(trigger) == "interval[4:00:00]"
    assert fake.job_kwargs["max_instances"] == 1


def test_scheduler_records_start_in_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_scheduler(monkeypatch, FakeScheduler(error=SystemExit(0)), FakeHealthServer())
    heartbeat_path = tmp_path / "hb" / "heartbeat.json"

    main_module.run_scheduler(
        Settings(heartbeat_path=heartbeat_path, health_host="127.0.0.1", health_port=0)
    )

    payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert payload["status"] == "starting"


def _clear_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Обнулить креды (переменные окружения важнее .env) и увести логи во временную папку."""
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "GIGACHAT_CREDENTIALS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))


@pytest.fixture()
def preserved_root_logger() -> Iterator[None]:
    """``main()`` перенастраивает корневой логгер — возвращаем его после теста."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


def test_once_without_credentials_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    preserved_root_logger: None,
) -> None:
    _clear_credentials(monkeypatch, tmp_path)

    exit_code = main_module.main(["run", "--once"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert (
        "configuration_error missing=TELEGRAM_BOT_TOKEN,TELEGRAM_CHANNEL_ID,GIGACHAT_CREDENTIALS"
        in output
    )
    # Ошибка настройки — не авария: трейсбек не печатается.
    assert "Traceback" not in output


def test_invalid_setting_value_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Ошибка возникает до настройки логирования, поэтому ловим её через caplog
    # (в production её выводит на stderr last-resort обработчик).
    _clear_credentials(monkeypatch, tmp_path)
    monkeypatch.setenv("MIN_NEWS_THRESHOLD", "not-a-number")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "top-secret-value")
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as info:
        main_module.main(["run", "--once"])

    messages = [record.getMessage() for record in caplog.records]
    assert info.value.code == 2
    assert "configuration_error invalid_fields=min_news_threshold" in messages
    # Значения переменных в лог не попадают: там могут быть секреты.
    assert not any("top-secret-value" in message for message in messages)


def test_dry_run_needs_no_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, preserved_root_logger: None
) -> None:
    _clear_credentials(monkeypatch, tmp_path)
    empty_db = tmp_path / "dry-run.sqlite"
    monkeypatch.setenv("DRY_RUN_DB_PATH", str(empty_db))
    monkeypatch.setenv("FEEDS", "[]")

    exit_code = main_module.main(["run", "--dry-run"])

    assert exit_code == 0
    assert empty_db.exists()
