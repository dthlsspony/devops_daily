"""Точка входа: сбор зависимостей, планировщик и CLI."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from types import FrameType

import httpx
from aiogram import Bot
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import ValidationError

from digest_bot.ca_bundle import build_ca_bundle
from digest_bot.collectors.rss import DEFAULT_USER_AGENT, RSSCollector
from digest_bot.config import Settings
from digest_bot.dedup import SQLiteStore
from digest_bot.health import Heartbeat, start_health_server
from digest_bot.logging_setup import setup_logging
from digest_bot.models import CycleResult, NewsItem
from digest_bot.pipeline import THRESHOLD_REASON, DigestPipeline
from digest_bot.publisher.telegram import TelegramPublisher
from digest_bot.summarizer.gigachat import GigaChatClient, GigaChatSummarizer
from digest_bot.tags import tag_icon

logger = logging.getLogger(__name__)

SCHEDULER_JOB_ID = "digest-cycle"


def build_parser() -> argparse.ArgumentParser:
    """Разобрать аргументы командной строки."""
    parser = argparse.ArgumentParser(
        prog="digest-bot",
        description="Дайджест новостей по информационной безопасности",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Запустить сбор и публикацию дайджеста")
    run_parser.add_argument(
        "--once",
        action="store_true",
        help="Выполнить один цикл и завершиться (без планировщика)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только собрать и показать новости, без GigaChat и Telegram (подразумевает --once)",
    )
    return parser


def load_settings() -> Settings:
    """Загрузить настройки из окружения.

    Неверное значение переменной — ошибка настройки (код выхода 2), а не авария.
    В лог попадают только имена полей: значения переменных могут быть секретами.
    """
    try:
        return Settings()
    except ValidationError as exc:
        fields = ",".join(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        logger.error("configuration_error invalid_fields=%s", fields)
        raise SystemExit(2) from None


def _check_credentials(settings: Settings) -> int | None:
    """Вернуть код выхода 2, если обязательные переменные не заданы."""
    missing = settings.missing_credentials()
    if missing:
        logger.error("configuration_error missing=%s", ",".join(missing))
        return 2
    return None


def build_gigachat_client(settings: Settings) -> GigaChatClient:
    """Собрать клиент GigaChat с проверкой TLS и нужными корневыми сертификатами."""
    if not settings.gigachat_verify_ssl:
        logger.warning("gigachat_tls_verification_disabled")
        return GigaChatClient(
            settings.gigachat_credentials,
            settings.gigachat_model,
            timeout=settings.request_timeout,
            verify_ssl_certs=False,
        )
    return GigaChatClient(
        settings.gigachat_credentials,
        settings.gigachat_model,
        timeout=settings.request_timeout,
        ca_bundle_file=build_ca_bundle(settings.ca_bundle_path),
    )


async def run_cycle(settings: Settings, *, dry_run: bool = False) -> CycleResult:
    """Выполнить один полный цикл пайплайна."""
    db_path = settings.dry_run_db_path if dry_run else settings.db_path

    async with httpx.AsyncClient(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=settings.request_timeout,
        follow_redirects=True,
    ) as client:
        collector = RSSCollector(
            settings.feeds,
            client=client,
            timeout=settings.request_timeout,
            max_items_per_feed=settings.max_items_per_feed,
        )

        bot: Bot | None = None
        chat_client: GigaChatClient | None = None
        publisher: TelegramPublisher | None = None
        summarizer: GigaChatSummarizer | None = None

        if not dry_run:
            bot = Bot(token=settings.telegram_bot_token)
            publisher = TelegramPublisher(
                bot,
                settings.telegram_channel_id,
                max_message_length=settings.telegram_max_message_length,
                rate_limit_delay=settings.telegram_rate_limit_delay,
            )
            chat_client = build_gigachat_client(settings)
            summarizer = GigaChatSummarizer(chat_client, settings.gigachat_model)

        try:
            with SQLiteStore(db_path) as store:
                pipeline = DigestPipeline(
                    collector=collector,
                    store=store,
                    summarizer=summarizer,
                    publisher=publisher,
                    keywords=settings.keywords,
                    tag_rules=settings.tag_rules,
                    min_news_threshold=settings.min_news_threshold,
                    max_item_age_days=settings.max_item_age_days,
                    max_items_per_tag=settings.max_items_per_tag,
                    max_posts_per_cycle=settings.max_posts_per_cycle,
                    min_items_per_tag=settings.min_items_per_tag,
                    dry_run=dry_run,
                )
                return await pipeline.run_cycle()
        finally:
            if bot is not None:
                await bot.session.close()
            if chat_client is not None:
                await chat_client.aclose()


def _print_dry_run(result: CycleResult, threshold: int) -> None:
    """Вывести в stdout посты, которые ушли бы в канал (режим --dry-run)."""
    if result.skipped_reason == THRESHOLD_REASON:
        sys.stdout.write(
            f"Порог не достигнут: новых новостей {result.new_items}, минимум {threshold}\n"
        )
    if not result.items:
        sys.stdout.flush()
        return
    by_tag: dict[str, list[NewsItem]] = {}
    for item in result.items:
        by_tag.setdefault(item.tag or "прочее", []).append(item)
    for tag, items in by_tag.items():
        sys.stdout.write(f"\n{tag_icon(tag)} {tag} — {len(items)} новостей\n")
        for index, item in enumerate(items, start=1):
            sys.stdout.write(f"  {index}. {item.title}\n     {item.source}: {item.link}\n")
    sys.stdout.write(
        f"\ncollected={result.collected} matched={result.matched} "
        f"new={result.new_items} selected={result.selected} posts={result.posts}\n"
    )
    sys.stdout.flush()


def _scheduled_cycle(settings: Settings, heartbeat: Heartbeat) -> None:
    """Задача планировщика: один цикл с обновлением heartbeat."""
    try:
        result = asyncio.run(run_cycle(settings))
    except Exception as exc:
        logger.exception("cycle_failed error=%s", type(exc).__name__)
        heartbeat.record_failure(error=type(exc).__name__)
        return
    heartbeat.record_success(
        items=result.selected,
        posts=result.posts,
        tags=",".join(result.tags),
        sent=result.published,
        skipped=result.skipped_reason,
    )


def _request_shutdown(signum: int, frame: FrameType | None) -> None:
    """Обработчик SIGTERM: завершить работу аккуратно, а не убивать процесс."""
    logger.info("shutdown_requested signal=%s", signum)
    raise SystemExit(0)


def run_scheduler(settings: Settings) -> None:
    """Запустить планировщик: первый цикл сразу, дальше — раз в ``SCHEDULE_HOURS``."""
    heartbeat = Heartbeat(settings.heartbeat_path)
    heartbeat.record_start()
    server = start_health_server(
        settings.health_host,
        settings.health_port,
        heartbeat,
        settings.heartbeat_max_age,
    )

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        _scheduled_cycle,
        trigger=IntervalTrigger(hours=settings.schedule_hours, timezone="UTC"),
        # Первый цикл — сразу после старта, а не через SCHEDULE_HOURS.
        next_run_time=datetime.now(tz=UTC),
        args=(settings, heartbeat),
        id=SCHEDULER_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("scheduler_started interval_hours=%d", settings.schedule_hours)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler_stopped")
    finally:
        # BlockingScheduler крутит цикл в главном потоке и при выходе не
        # останавливается сам. Без shutdown потоки исполнителей не дадут процессу
        # завершиться, и docker stop закончится SIGKILL. wait=True даёт уже
        # идущему циклу доделать работу в пределах stop_grace_period.
        if scheduler.running:
            scheduler.shutdown(wait=True)
        server.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI."""
    # Как можно раньше: SIGTERM, пришедший до установки обработчика, теряется.
    signal.signal(signal.SIGTERM, _request_shutdown)
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(
        settings.log_level,
        log_dir=settings.log_dir,
        max_bytes=settings.log_file_max_bytes,
        backup_count=settings.log_file_backup_count,
    )

    if args.command != "run":
        logger.error("unknown_command command=%s", args.command)
        return 2

    if args.once or args.dry_run:
        if not args.dry_run:
            exit_code = _check_credentials(settings)
            if exit_code is not None:
                return exit_code
        try:
            result = asyncio.run(run_cycle(settings, dry_run=args.dry_run))
        except Exception:
            logger.exception("run_once_failed")
            return 1
        if args.dry_run:
            _print_dry_run(result, settings.min_news_threshold)
        else:
            logger.info(
                "run_once published=%s items=%d tokens=%d",
                result.published,
                result.selected,
                result.tokens,
            )
        return 0

    exit_code = _check_credentials(settings)
    if exit_code is not None:
        return exit_code
    run_scheduler(settings)
    return 0
