from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


DUPLICATE_MODE_DISTANCES = {
    "strict": 1,
    "normal": 3,
    "aggressive": 6,
}


@dataclass(slots=True) # автоматически создает и запонляет большое кол-во параметров в __init__
class CollectorConfig: # объект хранения настроек
    """Параметры поиска, скачивания и обработки изображений."""

    # Сколько изображений нужно сохранить для каждой папки-класса.
    target_images_per_class: int = 100

    # Максимальное количество результатов, получаемых для одного запроса.
    max_results_per_query: int = 100

    # Ограничения размеров изображения.
    min_width: int = 250
    min_height: int = 180
    max_side: int = 2200
    max_image_pixels: int = 40_000_000

    # Ограничения объёма скачиваемых данных.
    max_file_size_mb: int = 15
    max_dataset_size_mb: int = 10_000
    chunk_size_kb: int = 64

    # Форматы, которые разрешено открывать через Pillow.
    # Пользователь их не выбирает: все изображения всё равно сохраняются как JPEG.
    allowed_image_formats: tuple[str, ...] = (
        "JPEG",
        "PNG",
        "WEBP",
    )
    allow_animated_images: bool = False
    jpeg_quality: int = 92

    # Настройки поисковой библиотеки DDGS.
    # Технические параметры имеют безопасные значения и не показываются в CLI.
    search_backend: str = "auto"
    region: str = "us-en"
    safesearch: str = "moderate"
    type_image: str = "photo"
    search_timeout: int = 15
    search_timelimit: str | None = None  # d, w, m, y или None
    search_page: int = 1
    image_size: str | None = None
    image_color: str | None = None
    image_layout: str | None = None
    license_image: str | None = None

    # Паузы уменьшают нагрузку на сайты и поисковую систему.
    download_delay_seconds: float = 0.15
    query_delay_seconds: float = 1.0

    # Настройки проверки визуальных дубликатов.
    duplicate_mode: str = "normal"  # strict, normal или aggressive
    hash_size: int = 8
    duplicate_scope: str = "dataset"  # dataset или class

    # Сетевые таймауты и повторные попытки.
    connect_timeout: int = 5
    read_timeout: int = 10
    retry_count: int = 1
    retry_backoff_factor: float = 0.3
    max_redirects: int = 5

    # Безопасные сетевые настройки.
    # HTTP, локальные адреса, системные proxy и .netrc не настраиваются пользователем.
    allow_http: bool = False
    block_private_networks: bool = True
    allowed_ports: tuple[int, ...] = (443,)
    trust_environment: bool = False
    require_image_content_type: bool = True

    # Настройки метаданных.
    write_metadata: bool = True
    metadata_url_mode: str = "sanitized"  # sanitized, full или none
    store_search_queries: bool = True
    csv_formula_protection: bool = True
    write_config_snapshot: bool = True
    store_queries_in_config_snapshot: bool = False

    # Настройки вывода в терминал.
    show_queries_in_console: bool = True
    show_detailed_errors: bool = False

    # Если True, к имени датасета добавляется временная метка.
    timestamp_dataset_folder: bool = True

    # Нейтральный User-Agent, не содержащий имени, почты или пути пользователя.
    user_agent: str = "image-dataset-builder/0.3"

    @property
    def duplicate_hash_distance(self) -> int:
        """Возвращает числовой порог dHash для выбранного режима."""

        return DUPLICATE_MODE_DISTANCES[self.duplicate_mode]

    def validate(self) -> None:
        """Проверяет корректность параметров до запуска сетевой сессии"""

        integer_ranges = {
            "target_images_per_class": (self.target_images_per_class, 1, 10_000),
            "max_results_per_query": (self.max_results_per_query, 1, 1_000),
            "min_width": (self.min_width, 1, 20_000),
            "min_height": (self.min_height, 1, 20_000),
            "max_side": (self.max_side, 64, 20_000),
            "max_image_pixels": (self.max_image_pixels, 1, 200_000_000),
            "max_file_size_mb": (self.max_file_size_mb, 1, 1_000),
            "max_dataset_size_mb": (self.max_dataset_size_mb, 1, 1_000_000),
            "chunk_size_kb": (self.chunk_size_kb, 1, 16_384),
            "search_timeout": (self.search_timeout, 1, 300),
            "search_page": (self.search_page, 1, 100),
            "hash_size": (self.hash_size, 4, 32),
            "connect_timeout": (self.connect_timeout, 1, 300),
            "read_timeout": (self.read_timeout, 1, 600),
        }

        for field_name, (value, minimum, maximum) in integer_ranges.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} должен быть целым числом")
            if not minimum <= value <= maximum:
                raise ValueError(
                    f"{field_name} должен быть от {minimum} до {maximum}"
                )

        if self.max_side < max(self.min_width, self.min_height):
            raise ValueError(
                "max_side не должен быть меньше минимальной ширины или высоты"
            )

        if not 0 <= self.retry_count <= 10:
            raise ValueError("retry_count должен быть от 0 до 10")

        if not 0 <= self.retry_backoff_factor <= 60:
            raise ValueError("retry_backoff_factor должен быть от 0 до 60")

        if not 0 <= self.max_redirects <= 20:
            raise ValueError("max_redirects должен быть от 0 до 20")

        if not 0 <= self.download_delay_seconds <= 60:
            raise ValueError("download_delay_seconds должен быть от 0 до 60")

        if not 0 <= self.query_delay_seconds <= 300:
            raise ValueError("query_delay_seconds должен быть от 0 до 300")

        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality должен быть от 1 до 100")

        if self.duplicate_mode not in DUPLICATE_MODE_DISTANCES:
            raise ValueError(
                "duplicate_mode должен быть strict, normal или aggressive"
            )

        if self.duplicate_scope not in {"dataset", "class"}:
            raise ValueError("duplicate_scope должен быть dataset или class")

        if self.safesearch not in {"off", "moderate", "on"}:
            raise ValueError("safesearch должен быть off, moderate или on")

        if self.search_timelimit not in {None, "d", "w", "m", "y"}:
            raise ValueError("search_timelimit должен быть d, w, m, y или None")

        if self.type_image != "photo":
            raise ValueError("type_image в этой версии должен быть photo")

        if self.metadata_url_mode not in {"sanitized", "full", "none"}:
            raise ValueError(
                "metadata_url_mode должен быть sanitized, full или none"
            )

        safe_formats = {"JPEG", "PNG", "WEBP"}
        normalized_formats = tuple(
            str(image_format).strip().upper()
            for image_format in self.allowed_image_formats
            if str(image_format).strip()
        )

        if not normalized_formats:
            raise ValueError("allowed_image_formats не должен быть пустым")

        unsupported_formats = set(normalized_formats) - safe_formats
        if unsupported_formats:
            unsupported = ", ".join(sorted(unsupported_formats))
            raise ValueError("неподдерживаемые форматы: " + unsupported)

        self.allowed_image_formats = normalized_formats

        if self.allow_animated_images:
            raise ValueError("анимированные изображения в этой версии запрещены")

        if self.allow_http:
            raise ValueError("незашифрованные HTTP-ссылки запрещены")

        if not self.block_private_networks:
            raise ValueError("блокировка локальных и служебных IP должна быть включена")

        if self.trust_environment:
            raise ValueError("использование HTTP_PROXY и .netrc должно быть выключено")

        if not self.require_image_content_type:
            raise ValueError("проверка Content-Type image/* должна быть включена")

        normalized_ports = tuple(int(port) for port in self.allowed_ports)
        if normalized_ports != (443,):
            raise ValueError("разрешён только безопасный HTTPS-порт 443")
        self.allowed_ports = normalized_ports

        if not isinstance(self.region, str) or not self.region.strip():
            raise ValueError("region не должен быть пустым")

        if len(self.region.strip()) > 20:
            raise ValueError("region не должен быть длиннее 20 символов")

        if self.license_image not in {
            None,
            "any",
            "Public",
            "Share",
            "ShareCommercially",
            "Modify",
            "ModifyCommercially",
        }:
            raise ValueError("указан неизвестный фильтр лицензии DDGS")

        if not self.user_agent.strip():
            raise ValueError("user_agent не должен быть пустым")

    def to_dict(self) -> dict[str, Any]:
        """Преобразует настройки в словарь, подходящий для JSON."""

        result = asdict(self)
        result["allowed_image_formats"] = list(self.allowed_image_formats)
        result["allowed_ports"] = list(self.allowed_ports)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CollectorConfig":
        """Создаёт настройки из словаря и отклоняет неизвестные поля."""

        if not isinstance(data, dict):
            raise TypeError("collector должен быть JSON-объектом")

        known_fields = {field.name for field in fields(cls)}
        unknown_fields = set(data) - known_fields

        if unknown_fields:
            unknown = ", ".join(sorted(unknown_fields))
            raise ValueError(f"неизвестные параметры CollectorConfig: {unknown}")

        prepared = dict(data)

        if "allowed_image_formats" in prepared:
            prepared["allowed_image_formats"] = tuple(
                prepared["allowed_image_formats"]
            )

        if "allowed_ports" in prepared:
            prepared["allowed_ports"] = tuple(prepared["allowed_ports"])

        config = cls(**prepared)
        config.validate()
        return config
