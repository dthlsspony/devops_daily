"""Тесты CA-bundle для GigaChat."""

from __future__ import annotations

import hashlib
import ssl
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import certifi

from digest_bot.ca_bundle import RUSSIAN_TRUSTED_ROOT_CA_SHA256, build_ca_bundle, bundled_ca_pem


def _certifi_pem() -> str:
    return Path(certifi.where()).read_text(encoding="ascii")


def _attributes(cert: dict[str, Any], field: str) -> dict[str, str]:
    """Атрибуты имени (subject/issuer) в виде словаря.

    Имя — это кортеж RDN, каждый из которых — кортеж пар «атрибут, значение»;
    typeshed описывает эту структуру слишком широким типом.
    """
    rdns = cast("Iterable[Iterable[tuple[str, str]]]", cert[field])
    return {key: value for rdn in rdns for key, value in rdn}


def test_bundled_root_has_pinned_fingerprint() -> None:
    der = ssl.PEM_cert_to_DER_cert(bundled_ca_pem())

    assert hashlib.sha256(der).hexdigest() == RUSSIAN_TRUSTED_ROOT_CA_SHA256


def test_bundled_root_is_russian_trusted_root_ca() -> None:
    certs = ssl.create_default_context(cadata=bundled_ca_pem()).get_ca_certs()

    assert len(certs) == 1
    subject = _attributes(certs[0], "subject")
    issuer = _attributes(certs[0], "issuer")
    assert subject["commonName"] == "Russian Trusted Root CA"
    assert subject == issuer  # самоподписанный корень


def test_bundle_combines_certifi_and_russian_root(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "ca-bundle.pem"

    result = build_ca_bundle(target)

    assert result == target
    content = result.read_text(encoding="ascii")
    assert _certifi_pem().strip() in content
    assert bundled_ca_pem() in content
    assert content.count("-----BEGIN CERTIFICATE-----") == (
        _certifi_pem().count("-----BEGIN CERTIFICATE-----") + 1
    )


def test_bundle_is_trusted_by_ssl_and_contains_russian_root(tmp_path: Path) -> None:
    target = build_ca_bundle(tmp_path / "ca-bundle.pem")

    context = ssl.create_default_context(cafile=str(target))

    common_names = {
        name
        for cert in context.get_ca_certs()
        if (name := _attributes(cert, "subject").get("commonName")) is not None
    }
    assert "Russian Trusted Root CA" in common_names
    assert len(common_names) > 100


def test_bundle_is_not_rewritten_when_unchanged(tmp_path: Path) -> None:
    target = build_ca_bundle(tmp_path / "ca-bundle.pem")
    modified_at = target.stat().st_mtime_ns

    build_ca_bundle(target)

    assert target.stat().st_mtime_ns == modified_at


def test_bundle_is_rewritten_when_stale(tmp_path: Path) -> None:
    target = tmp_path / "ca-bundle.pem"
    target.write_text("stale", encoding="ascii")

    build_ca_bundle(target)

    assert bundled_ca_pem() in target.read_text(encoding="ascii")
