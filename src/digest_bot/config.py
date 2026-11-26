"""Конфигурация приложения: единственный источник правды для параметров.

Все значения читаются из переменных окружения (и ``.env``), секреты в коде
не хардкодятся. Значения по умолчанию описаны в ``.env.example``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Ограничение Telegram на длину одного сообщения.
TELEGRAM_MESSAGE_LIMIT = 4096

#: Запас поверх интервала цикла, после которого heartbeat считается протухшим.
HEARTBEAT_GRACE_SECONDS = 15 * 60


class Feed(BaseModel):
    """Одна RSS-лента: короткое имя источника и его URL."""

    source: str
    url: str


#: Фиды по умолчанию — официальные источники и агрегаторы по ИБ.
#:
#: Отличия от первоначального списка в плане (проверено на реальных лентах):
#: - ``nginx.org`` не публикует RSS (запланированный ``nginx.org/blog/rss.xml``
#:   отдаёт 404). Новости nginx попадают в дайджест из агрегаторов по
#:   ключевому слову ``nginx``; при желании добавьте свой фид через ``FEEDS``.
#: - для Node.js берётся фид security-advisories: общий блог отдаёт архив
#:   на 1000+ записей, где совпадает каждый заголовок.
DEFAULT_FEEDS: tuple[Feed, ...] = (
    Feed(source="thehackernews", url="https://feeds.feedburner.com/TheHackersNews"),
    Feed(source="bleepingcomputer", url="https://www.bleepingcomputer.com/feed/"),
    Feed(source="php.net", url="https://www.php.net/releases/feed.php"),
    Feed(source="nodejs", url="https://nodejs.org/en/feed/vulnerability.xml"),
)

#: Ключевые слова, по которым новость попадает в дайджест.
DEFAULT_KEYWORDS: tuple[str, ...] = (
    "php",
    "linux",
    "nginx",
    "node.js",
    "nodejs",
    "npm",
    "cve",
    "rce",
    "xss",
    "sql injection",
    "vulnerability",
    "exploit",
    "malware",
    "backdoor",
    "zero-day",
    "security patch",
    "security release",
)


class Settings(BaseSettings):
    """Параметры сервиса, загруженные из окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- обязательные (проверяются в рабочем режиме) ---------------------
    telegram_bot_token: str = ""
    telegram_channel_id: str = ""
    gigachat_credentials: str = ""
    gigachat_model: str = "GigaChat-2-Max"
    #: Проверка TLS к GigaChat. Отключайте только понимая, что ключ GigaChat
    #: уйдёт по непроверенному соединению.
    gigachat_verify_ssl: bool = True
    #: Куда собрать единый CA-bundle (стандартные корни + Минцифры).
    ca_bundle_path: Path = Path("./data/ca-bundle.pem")

    # --- поведение пайплайна ---------------------------------------------
    min_news_threshold: int = 2
    schedule_hours: int = 4
    db_path: Path = Path("./data/seen.sqlite")
    dry_run_db_path: Path = Path("./data/seen.dry-run.sqlite")

    # --- ограничения объёма -----------------------------------------------
    # Не дают фид-архиву или скоплению новостей за простой превратиться
    # в десятки вызовов GigaChat. Значение 0 — без ограничения.
    #: Сколько последних записей брать из одной ленты.
    max_items_per_feed: int = 50
    #: Новости старше этого числа дней в дайджест не попадают.
    max_item_age_days: int | None = 3
    #: Максимум новостей в одном дайджесте; остальные дождутся следующего цикла.
    max_items_per_digest: int | None = 30

    # --- сеть -------------------------------------------------------------
    request_timeout: float = 20.0
    telegram_max_message_length: int = TELEGRAM_MESSAGE_LIMIT
    telegram_rate_limit_delay: float = 1.0

    # --- наблюдаемость ----------------------------------------------------
    log_level: str = "INFO"
    log_dir: Path = Path("./data/logs")
    log_file_max_bytes: int = 5 * 1024 * 1024
    log_file_backup_count: int = 5
    health_host: str = "0.0.0.0"
    health_port: int = 8080
    heartbeat_path: Path = Path("./data/heartbeat.json")
    #: Явный лимит возраста heartbeat; по умолчанию — интервал цикла + запас.
    heartbeat_max_age_seconds: int | None = None

    # --- источники и фильтр ------------------------------------------------
    feeds: list[Feed] = Field(default_factory=lambda: list(DEFAULT_FEEDS))
    keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_KEYWORDS))

    def missing_credentials(self) -> list[str]:
        """Список обязательных переменных, которые не заданы."""
        required = {
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "TELEGRAM_CHANNEL_ID": self.telegram_channel_id,
            "GIGACHAT_CREDENTIALS": self.gigachat_credentials,
        }
        return [name for name, value in required.items() if not value.strip()]

    @property
    def heartbeat_max_age(self) -> int:
        """Допустимый возраст heartbeat в секундах.

        По умолчанию зависит от интервала цикла: между запусками heartbeat
        не обновляется, и фиксированный короткий лимит всегда был бы «протухшим».
        """
        if self.heartbeat_max_age_seconds is not None:
            return self.heartbeat_max_age_seconds
        return self.schedule_hours * 3600 + HEARTBEAT_GRACE_SECONDS

    @field_validator("max_item_age_days", "max_items_per_digest", mode="before")
    @classmethod
    def _zero_means_disabled(cls, value: object) -> object:
        """Значение ``0`` в окружении означает «без ограничения»."""
        return None if value in (0, "0") else value
