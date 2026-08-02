"""Проверяет данные, которые были получены от пользователя(в cli), перед их использованием"""


from __future__ import annotations

import ipaddress
import socket
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import CollectorConfig


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

_INVALID_WINDOWS_CHARACTERS = set('<>:"/\\|?*')


def validate_path_component(value: str, field_name: str = "название") -> str:
    """Проверяет, что строка является безопасным именем одной папки."""

    if not isinstance(value, str):
        raise TypeError(f"{field_name} должно быть строкой")

    normalized = unicodedata.normalize("NFC", value).strip()

    if not normalized:
        raise ValueError(f"{field_name} не должно быть пустым")

    if len(normalized) > 100:
        raise ValueError(f"{field_name} не должно быть длиннее 100 символов")

    if normalized in {".", ".."}:
        raise ValueError(f"{field_name} не может быть . или ..")

    if any(character in _INVALID_WINDOWS_CHARACTERS for character in normalized):
        raise ValueError(
            f"{field_name} содержит запрещённый символ пути: "
            '< > : " / \\ | ? *'
        )

    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{field_name} содержит управляющий символ")

    if normalized.endswith((" ", ".")):
        raise ValueError(f"{field_name} не должно оканчиваться пробелом или точкой")

    first_part = normalized.split(".", maxsplit=1)[0].upper()

    if first_part in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{field_name} является служебным именем Windows")

    return normalized


def validate_query(value: str) -> str:
    """Проверяет поисковый запрос без выполнения кода или команд оболочки."""

    if not isinstance(value, str):
        raise TypeError("поисковый запрос должен быть строкой")

    query = unicodedata.normalize("NFC", value).strip()

    if not query:
        raise ValueError("поисковый запрос не должен быть пустым")

    if len(query) > 500:
        raise ValueError("поисковый запрос не должен быть длиннее 500 символов")

    if any(character in "\r\n\x00" for character in query):
        raise ValueError("поисковый запрос содержит управляющий символ")

    return query


def validate_classes(classes: dict[str, list[str]]) -> dict[str, list[str]]:
    """Проверяет названия папок и соответствующие им поисковые запросы."""

    if not isinstance(classes, dict) or not classes:
        raise ValueError("словарь classes не должен быть пустым")

    if len(classes) > 1000:
        raise ValueError("разрешено не более 1000 папок-классов")

    normalized_classes: dict[str, list[str]] = {}
    used_casefold_names: set[str] = set()

    for raw_class_name, raw_queries in classes.items():
        class_name = validate_path_component(
            raw_class_name,
            field_name="название папки-класса",
        )

        casefold_name = class_name.casefold()

        if casefold_name in used_casefold_names:
            raise ValueError(
                "названия папок не должны различаться только регистром букв"
            )

        if not isinstance(raw_queries, list) or not raw_queries:
            raise ValueError(
                f"для папки {class_name!r} нужен хотя бы один поисковый запрос"
            )

        if len(raw_queries) > 1000:
            raise ValueError(
                f"для папки {class_name!r} разрешено не более 1000 запросов"
            )

        queries = [validate_query(query) for query in raw_queries]

        normalized_classes[class_name] = queries
        used_casefold_names.add(casefold_name)

    return normalized_classes


def ensure_child_path(parent: Path, child: Path) -> None:
    """Проверяет, что путь находится внутри родительской папки."""

    parent_resolved = parent.resolve(strict=False)
    child_resolved = child.resolve(strict=False)

    if not child_resolved.is_relative_to(parent_resolved):
        raise ValueError(f"небезопасный путь вне папки датасета: {child}")


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global


def validate_download_url(url: str, config: CollectorConfig) -> str:
    """Проверяет URL и блокирует обращения к локальной сети и служебным адресам."""

    if not isinstance(url, str) or not url.strip():
        raise ValueError("пустой URL изображения")

    clean_url = url.strip()

    if len(clean_url) > 4096:
        raise ValueError("URL изображения слишком длинный")

    if any(character in clean_url for character in "\r\n\x00"):
        raise ValueError("URL содержит управляющий символ")

    parsed = urlsplit(clean_url)
    allowed_schemes = {"https"}

    if config.allow_http:
        allowed_schemes.add("http")

    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError("разрешены только безопасные HTTP(S)-ссылки")

    if not parsed.hostname:
        raise ValueError("в URL отсутствует имя сервера")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL со встроенным логином или паролем запрещён")

    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("в URL указан некорректный порт") from error

    effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)

    if effective_port not in config.allowed_ports:
        raise ValueError(f"порт {effective_port} не разрешён настройками")

    hostname = parsed.hostname.rstrip(".").lower()

    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("локальные адреса запрещены")

    if hostname.endswith(".local"):
        raise ValueError("локальные домены .local запрещены")

    if config.block_private_networks:
        try:
            addresses = socket.getaddrinfo(
                hostname,
                effective_port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as error:
            raise ValueError("не удалось определить IP-адрес сервера") from error

        resolved_addresses = {
            address_info[4][0]
            for address_info in addresses
        }

        if not resolved_addresses:
            raise ValueError("сервер не вернул IP-адрес")

        if any(not _is_public_ip(address) for address in resolved_addresses):
            raise ValueError("ссылка ведёт в локальную или служебную сеть")

    # Фрагмент после # не отправляется серверу и не нужен для скачивания.
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def sanitize_url_for_metadata(url: str, mode: str) -> str:
    """Удаляет из URL потенциально чувствительные параметры перед записью в CSV."""

    if mode == "none" or not url:
        return ""

    if mode == "full":
        return url

    parsed = urlsplit(url)

    if not parsed.hostname:
        return ""

    hostname = parsed.hostname

    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    netloc = hostname

    try:
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
    except ValueError:
        return ""

    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            "",
            "",
        )
    )
