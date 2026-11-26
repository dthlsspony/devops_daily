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
    """Одна RSS-лента: короткое имя источника и его URL.

    ``trusted`` — все новости ленты релевантны по умолчанию, даже если в них
    нет слов про безопасность. Так помечаются официальные advisory и релизы
    (php.net, Node.js, USN Ubuntu, CISA): их тема известна заранее.
    """

    source: str
    url: str
    trusted: bool = False


class TagRule(BaseModel):
    """Правило присвоения тега.

    Срабатывает, если в заголовке новости найдено одно из ``keywords`` **или**
    если источник новости указан в ``sources`` (тег-фолбэк для лент, чьи
    заголовки не содержат имени стека).
    """

    tag: str
    keywords: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


#: Фиды по умолчанию — только проверенные живые ленты (ноябрь 2026).
#:
#: Отличия от первоначального списка в плане:
#: - ``nginx.org`` не публикует RSS (запланированный ``nginx.org/blog/rss.xml``
#:   отдаёт 404). Новости nginx попадают в дайджест из агрегаторов по
#:   ключевому слову ``nginx``; при желании добавьте свой фид через ``FEEDS``.
#: - для Node.js берётся фид security-advisories: общий блог отдаёт архив
#:   на 1000+ записей, где совпадает каждый заголовок.
#: - общий фид WordPress добавлен как источник PHP-стека.
DEFAULT_FEEDS: tuple[Feed, ...] = (
    # --- агрегаторы новостей ИБ ---
    Feed(source="thehackernews", url="https://feeds.feedburner.com/TheHackersNews"),
    Feed(source="bleepingcomputer", url="https://www.bleepingcomputer.com/feed/"),
    Feed(source="helpnetsecurity", url="https://www.helpnetsecurity.com/feed/"),
    Feed(source="securityweek", url="https://www.securityweek.com/feed/"),
    Feed(source="krebs", url="https://krebsonsecurity.com/feed/"),
    Feed(source="darkreading", url="https://www.darkreading.com/rss.xml"),
    # --- стек ---
    Feed(source="php.net", url="https://www.php.net/releases/feed.php", trusted=True),
    Feed(source="wordpress", url="https://wordpress.org/news/feed/"),
    Feed(source="nodejs", url="https://nodejs.org/en/feed/vulnerability.xml", trusted=True),
    Feed(source="python", url="https://blog.python.org/feeds/posts/default"),
    Feed(source="linuxjournal", url="https://www.linuxjournal.com/node/feed"),
    Feed(source="phoronix", url="https://www.phoronix.com/rss.php"),
    Feed(source="lwn", url="https://lwn.net/headlines/rss"),
    Feed(source="ubuntu", url="https://ubuntu.com/security/notices/rss.xml", trusted=True),
    Feed(source="redhat", url="https://www.redhat.com/en/rss/blog"),
    Feed(source="kubernetes", url="https://kubernetes.io/feed.xml"),
    Feed(source="docker", url="https://www.docker.com/blog/feed/"),
    Feed(source="github", url="https://github.blog/feed/"),
    Feed(source="gitlab", url="https://about.gitlab.com/atom.xml"),
    # --- облако и инфраструктура ---
    Feed(source="aws", url="https://aws.amazon.com/blogs/security/feed/"),
    Feed(source="cloudflare", url="https://blog.cloudflare.com/rss/"),
    # --- уязвимости, advisories, исследования ---
    Feed(source="cisa", url="https://www.cisa.gov/cybersecurity-advisories/all.xml", trusted=True),
    Feed(source="talos", url="https://blog.talosintelligence.com/rss/"),
    Feed(source="portswigger", url="https://portswigger.net/research/rss"),
    Feed(source="snyk", url="https://snyk.io/blog/feed/"),
    Feed(source="projectzero", url="https://googleprojectzero.blogspot.com/feeds/posts/default"),
)

#: Ключевые слова, по которым новость попадает в дайджест.
#:
#: Только признаки события безопасности. Названия стеков (``linux``, ``php``,
#: ``node.js``) здесь не нужны: иначе любые новости о стеке — включая
#: бенчмарки и обзоры — попадали бы в дайджест. Стек выражается тегом.
DEFAULT_KEYWORDS: tuple[str, ...] = (
    "cve",
    "rce",
    "remote code execution",
    "vulnerability",
    "exploit",
    "exploited",
    "malware",
    "ransomware",
    "trojan",
    "botnet",
    "backdoor",
    "xss",
    "sql injection",
    "injection",
    "zero-day",
    "breach",
    "data leak",
    "leaked",
    "unpatched",
    "authentication bypass",
    "privilege escalation",
    "denial of service",
    "security update",
    "security patch",
    "security release",
    "security advisory",
)

#: Правила тегирования по умолчанию. Порядок = приоритет:
#: отличительный тип события → стек → общий тип события.
#:
#: Каждая новость получает ровно один тег, поэтому между постами нет повторов.
DEFAULT_TAG_RULES: tuple[TagRule, ...] = (
    # --- отличительный тип события ---
    TagRule(
        tag="supply-chain",
        keywords=[
            "supply chain",
            "malicious package",
            "malicious module",
            "malicious npm",
            "malicious pypi",
            "typosquatting",
            "dependency confusion",
            "poisoned",
            "backdoored",
            "package registry",
        ],
        sources=["snyk"],
    ),
    TagRule(
        tag="malware",
        keywords=[
            "malware",
            "ransomware",
            "botnet",
            "trojan",
            "infostealer",
            "stealer",
            "cryptominer",
            "wiper",
            "rootkit",
            "keylogger",
            "backdoor",
            "spyware",
            "dropper",
        ],
    ),
    TagRule(
        tag="leaks",
        keywords=[
            "data breach",
            "data leak",
            "leaked",
            "exfiltrat",
            "credentials stolen",
            "database dump",
            "breach",
        ],
    ),
    # --- стек ---
    TagRule(
        tag="php",
        keywords=["php", "composer", "symfony", "laravel", "wordpress", "drupal", "magento"],
        sources=["php.net", "wordpress"],
    ),
    TagRule(
        tag="nodejs",
        keywords=[
            "node.js",
            "nodejs",
            "npm",
            "javascript",
            "typescript",
            "react",
            "next.js",
            "express",
        ],
        sources=["nodejs"],
    ),
    TagRule(
        tag="linux",
        keywords=[
            "linux",
            "ubuntu",
            "debian",
            "kernel",
            "systemd",
            "openssh",
            "fedora",
            "red hat",
            "suse",
        ],
        sources=["linuxjournal", "phoronix", "lwn", "ubuntu", "redhat"],
    ),
    TagRule(tag="nginx", keywords=["nginx", "openresty"]),
    TagRule(
        tag="web",
        keywords=[
            "browser",
            "chrome",
            "firefox",
            "safari",
            "webkit",
            "apache",
            "tomcat",
            "wordpress",
            "http",
        ],
    ),
    TagRule(
        tag="cloud",
        keywords=[
            "aws",
            "azure",
            "google cloud",
            "gcp",
            "kubernetes",
            "docker",
            "cloudflare",
            "terraform",
            "s3 bucket",
            "ec2",
        ],
        sources=["aws", "cloudflare", "kubernetes", "docker"],
    ),
    TagRule(
        tag="windows", keywords=["windows", "microsoft", ".net", "powershell", "iis", "exchange"]
    ),
    TagRule(tag="apple", keywords=["apple", "macos", "ios", "xcode"]),
    TagRule(tag="google", keywords=["google", "android"]),
    TagRule(
        tag="devops",
        keywords=["gitlab", "github action", "ci/cd", "devops", "sre", "ansible", "jenkins"],
        sources=["github", "gitlab"],
    ),
    TagRule(
        tag="python", keywords=["python", "django", "flask", "pip", "pypi"], sources=["python"]
    ),
    # --- общий тип события ---
    TagRule(
        tag="exploits",
        keywords=[
            "rce",
            "exploit",
            "zero-day",
            "actively exploited",
            "in the wild",
            "proof of concept",
            "unauthenticated",
            "arbitrary code execution",
            "remote code execution",
            "privilege escalation",
            "path traversal",
            "deserialization",
            "bypass",
        ],
        sources=["talos"],
    ),
    TagRule(
        tag="advisories",
        keywords=["advisory", "advisories", "security update", "security release", "end-of-life"],
        sources=["cisa"],
    ),
    TagRule(
        tag="policy",
        keywords=[
            "regulation",
            "sanctions",
            "compliance",
            "nist",
            "europol",
            "gdpr",
            "law enforcement",
        ],
    ),
    TagRule(
        tag="research",
        keywords=["researcher", "research", "novel", "technique", "reverse engineering", "study"],
        sources=["portswigger", "projectzero"],
    ),
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
    #: Максимум новостей в одном посте (одном теге).
    max_items_per_tag: int | None = 15
    #: Максимум постов за цикл: столько же вызовов GigaChat.
    max_posts_per_cycle: int | None = 5
    #: Минимум новостей в посте; меньше — тег ждёт следующего цикла.
    min_items_per_tag: int = 1

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

    # --- источники, фильтр и теги ------------------------------------------
    feeds: list[Feed] = Field(default_factory=lambda: list(DEFAULT_FEEDS))
    keywords: list[str] = Field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    tag_rules: list[TagRule] = Field(default_factory=lambda: list(DEFAULT_TAG_RULES))

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

    @field_validator(
        "max_item_age_days",
        "max_items_per_tag",
        "max_posts_per_cycle",
        mode="before",
    )
    @classmethod
    def _zero_means_disabled(cls, value: object) -> object:
        """Значение ``0`` в окружении означает «без ограничения»."""
        return None if value in (0, "0") else value
