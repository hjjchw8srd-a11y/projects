"""получает url изображения -> скачивает его -> проверяет, нормальное ли оно -> возвращает следующей ф-ии
"""

from __future__ import annotations

from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import CollectorConfig
from .image_utils import prepare_image
from .validation import validate_download_url


_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class DownloadError(RuntimeError):
    """Безопасная ошибка скачивания без вывода полного URL."""


class DatasetSizeLimitError(RuntimeError):
    """Общий размер датасета достиг установленного ограничения."""


def create_http_session(config: CollectorConfig) -> requests.Session: # используется Session вместо get, чтобы не загружать скрипт сотнями запросов get
    """Создаёт HTTP-сессию с таймаутами и ограниченными повторами."""

    retry = Retry(
        total=config.retry_count,
        connect=config.retry_count,
        read=config.retry_count,
        status=config.retry_count,
        backoff_factor=config.retry_backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )

    session = requests.Session()

    # Не используем автоматически переменные HTTP_PROXY и файл .netrc.
    # Это снижает риск случайной отправки локальных учётных данных.
    session.trust_env = config.trust_environment

    session.headers.update(
        {
            "User-Agent": config.user_agent,
            "Accept": "image/*",
        }
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def _request_with_safe_redirects(
    session: requests.Session,
    image_url: str,
    config: CollectorConfig,
) -> requests.Response:
    """Проверяет исходный URL и каждый адрес перенаправления."""

    current_url = image_url

    for redirect_number in range(config.max_redirects + 1):
        current_url = validate_download_url(current_url, config)

        try:
            response = session.get(
                current_url,
                timeout=(config.connect_timeout, config.read_timeout),
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            raise DownloadError(
                f"ошибка сети: {type(error).__name__}"
            ) from error

        if response.status_code not in _REDIRECT_STATUS_CODES:
            return response

        location = response.headers.get("Location")
        response.close()

        if not location:
            raise DownloadError("сервер вернул перенаправление без адреса")

        if redirect_number >= config.max_redirects:
            raise DownloadError("превышено максимальное количество перенаправлений")

        current_url = urljoin(current_url, location)

    raise DownloadError("не удалось завершить перенаправление")


def download_image(
    session: requests.Session,
    image_url: str,
    config: CollectorConfig,
):
    """Скачивает изображение частями и проверяет ограничения до обработки."""

    response = _request_with_safe_redirects(
        session=session,
        image_url=image_url,
        config=config,
    )

    with response:
        if response.status_code >= 400:
            raise DownloadError(f"сервер вернул HTTP {response.status_code}")

        content_type = response.headers.get("Content-Type", "")
        normalized_content_type = content_type.split(";", maxsplit=1)[0].lower()

        if (
            config.require_image_content_type
            and not normalized_content_type.startswith("image/")
        ):
            raise DownloadError("сервер вернул данные не с типом image/*")

        max_bytes = config.max_file_size_mb * 1024 * 1024
        content_length = response.headers.get("Content-Length")

        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0

            if declared_size > max_bytes:
                raise DownloadError("файл слишком большой")

        content = bytearray()
        chunk_size = config.chunk_size_kb * 1024

        # Изображение скачивается частями, чтобы не загружать без ограничений.
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue

            content.extend(chunk)

            if len(content) > max_bytes:
                raise DownloadError("файл превысил допустимый размер")

    return prepare_image(bytes(content), config)
