"""Доверенные корневые сертификаты для GigaChat.

OAuth-сервер GigaChat (``ngw.devices.sberbank.ru``) использует цепочку
«Russian Trusted Root CA» Минцифры. Этого корня нет в стандартном хранилище
``certifi``, поэтому проверка TLS падает с ``CERTIFICATE_VERIFY_FAILED``.
Официальная документация GigaChat рекомендует передавать корень в клиент
через ``ca_bundle_file``.

SDK применяет ``ca_bundle_file`` и к OAuth-серверу, и к API, поэтому в bundle
должны быть и стандартные корни (для ``api.giga.chat``), и русский корень.

Источник сертификата: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
"""

from __future__ import annotations

import hashlib
import logging
import os
import ssl
from importlib import resources
from pathlib import Path

import certifi

logger = logging.getLogger(__name__)

#: Корневой сертификат Минцифры, входит в состав пакета.
RUSSIAN_TRUSTED_ROOT_CA = "russian_trusted_root_ca.pem"

#: Отпечаток SHA-256 сертификата выше (DER). Сверен с корнем, который отдаёт
#: OAuth-сервер GigaChat в цепочке TLS.
RUSSIAN_TRUSTED_ROOT_CA_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


def bundled_ca_pem() -> str:
    """Прочитать сертификат Минцифры из состава пакета и проверить целостность."""
    pem = (
        resources.files("digest_bot")
        .joinpath("certs", RUSSIAN_TRUSTED_ROOT_CA)
        .read_text(encoding="ascii")
        .strip()
    )
    der = ssl.PEM_cert_to_DER_cert(pem)
    if hashlib.sha256(der).hexdigest() != RUSSIAN_TRUSTED_ROOT_CA_SHA256:
        raise RuntimeError("Сертификат Russian Trusted Root CA в пакете повреждён")
    return pem


def build_ca_bundle(target: Path) -> Path:
    """Собрать единый CA-bundle (стандартные корни + Минцифры) в файле ``target``.

    Файл перезаписывается только при изменении содержимого, поэтому вызов
    дёшев и безопасен на каждом запуске.
    """
    certifi_bundle = Path(certifi.where()).read_text(encoding="ascii").rstrip()
    content = f"{certifi_bundle}\n{bundled_ca_pem()}\n"

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.read_text(encoding="ascii") == content:
        return target

    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="ascii")
    temporary.replace(target)
    logger.info("ca_bundle_written path=%s", target)
    return target
