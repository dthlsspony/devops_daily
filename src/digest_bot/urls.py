"""Канонизация ссылок — общий ключ дедупликации.

Правило одно и используется везде: сборщиком (чтобы сохранять в базу) и
хранилищем (чтобы привести уже сохранённые ссылки к тому же виду при
обновлении программы). Иначе старые и новые записи перестают совпадать, и
уже опубликованные новости публикуются заново.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Параметры, которые не различают статьи между собой.
TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})


def canonical_link(url: str) -> str:
    """Привести URL к каноническому виду (ключ дедупликации).

    Убираются якорь, трекинговые параметры и завершающий слеш; схема
    приводится к https, а префикс ``www.`` отбрасывается. Иначе одна и та же
    статья, на которую сослались как ``http://www.example.com/a`` и как
    ``https://example.com/a``, получила бы два разных ключа дедупликации и
    попала бы в два тега.
    """
    cleaned = url.strip()
    if not cleaned:
        return ""
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return cleaned
    if not parts.scheme or not parts.netloc:
        return cleaned
    try:
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return cleaned
    if host.startswith("www."):
        host = host[4:]
    netloc = host if port is None else f"{host}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    scheme = "https" if parts.scheme.lower() in ("http", "https") else parts.scheme.lower()
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))
