"""управляет ходом работы скрипта"""


from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .config import CollectorConfig
from .downloader import (
    DatasetSizeLimitError,
    DownloadError,
    create_http_session,
    download_image,
)
from .hashing import difference_hash, is_duplicate
from .metadata import append_metadata
from .validation import (
    ensure_child_path,
    sanitize_url_for_metadata,
    validate_classes,
    validate_path_component,
)


DATASET_MARKER_NAME = ".image_dataset_builder.json"
CONFIG_SNAPSHOT_NAME = "dataset_settings.json"


@dataclass(slots=True)
class _CollectionState:
    saved_bytes: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    """Записывает JSON и по возможности ограничивает права локального файла."""

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    try:
        path.chmod(0o600)
    except OSError:
        pass


def _create_dataset_root(
    output_dir: str | Path,
    class_names: list[str],
    dataset_name: str,
    config: CollectorConfig,
) -> Path:
    """Создаёт новую безопасную папку датасета."""

    safe_dataset_name = validate_path_component(
        dataset_name,
        field_name="название датасета",
    )

    output_path = Path(output_dir).expanduser()

    if output_path.exists() and output_path.is_symlink():
        raise ValueError("output_dir не должен быть символической ссылкой")

    output_path.mkdir(parents=True, exist_ok=True)

    if config.timestamp_dataset_folder:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{safe_dataset_name}_{timestamp}"
    else:
        folder_name = safe_dataset_name

    dataset_root = output_path / folder_name
    ensure_child_path(output_path, dataset_root)

    if dataset_root.exists():
        raise FileExistsError(
            f"папка датасета уже существует и не будет перезаписана: {dataset_root}"
        )

    dataset_root.mkdir(parents=True)

    for split_name in ("raw", "train", "test", "rejected"):
        split_dir = dataset_root / split_name
        ensure_child_path(dataset_root, split_dir)
        split_dir.mkdir(parents=True, exist_ok=False)

    # Классы заранее создаются только в raw и rejected. Папки train/test
    # формирует splitter после ручной проверки изображений.
    for split_name in ("raw", "rejected"):
        for class_name in class_names:
            class_dir = dataset_root / split_name / class_name
            ensure_child_path(dataset_root, class_dir)
            class_dir.mkdir(parents=True, exist_ok=False)

    marker = {
        "format_version": 1,
        "created_at_utc": _utc_now(),
        "dataset_name": safe_dataset_name,
        "class_names": class_names,
    }
    _write_private_json(dataset_root / DATASET_MARKER_NAME, marker)

    return dataset_root


def _write_config_snapshot(
    dataset_root: Path,
    dataset_name: str,
    classes: dict[str, list[str]],
    config: CollectorConfig,
) -> None:
    """Сохраняет настройки без абсолютного пути компьютера пользователя."""

    if not config.write_config_snapshot:
        return

    if config.store_queries_in_config_snapshot:
        classes_snapshot: dict[str, Any] = classes
    else:
        classes_snapshot = {
            class_name: {"query_count": len(queries)}
            for class_name, queries in classes.items()
        }

    snapshot = {
        "dataset_name": dataset_name,
        "classes": classes_snapshot,
        "collector": config.to_dict(),
    }

    _write_private_json(dataset_root / CONFIG_SNAPSHOT_NAME, snapshot)


def _search_images(query: str, config: CollectorConfig):
    """Возвращает результаты поиска изображений через библиотеку DDGS."""

    # Импорт выполняется только при реальном запуске поиска.
    from ddgs import DDGS

    search_parameters: dict[str, Any] = {
        "query": query,
        "region": config.region,
        "safesearch": config.safesearch,
        "max_results": config.max_results_per_query,
        "backend": config.search_backend,
        "type_image": config.type_image,
        "page": config.search_page,
    }

    # Не передаём пустые фильтры: разные backend-ы DDGS могут обрабатывать их
    # по-разному.
    optional_parameters = {
        "timelimit": config.search_timelimit,
        "size": config.image_size,
        "color": config.image_color,
        "layout": config.image_layout,
        "license_image": config.license_image,
    }
    search_parameters.update(
        {
            name: value
            for name, value in optional_parameters.items()
            if value is not None
        }
    )

    return DDGS(
        proxy=None,
        timeout=config.search_timeout,
        verify=True,
    ).images(**search_parameters)


def _next_image_index(class_dir: Path) -> int:
    existing_numbers = [
        int(path.stem)
        for path in class_dir.glob("*.jpg")
        if path.stem.isdigit() and not path.is_symlink()
    ]

    return max(existing_numbers, default=0) + 1


def _safe_error_text(error: Exception, detailed: bool) -> str:
    """Возвращает понятную причину ошибки без вывода полного URL."""

    if isinstance(error, DownloadError):
        return str(error)

    if isinstance(error, UnidentifiedImageError):
        return "файл не распознан как изображение"

    if isinstance(error, Image.DecompressionBombError):
        return "изображение содержит опасно большое количество пикселей"

    if isinstance(error, ValueError):
        return str(error) or "некорректные данные изображения"

    if isinstance(error, OSError):
        return str(error) or "ошибка чтения или сохранения изображения"

    if detailed:
        return f"{type(error).__name__}: {error}"

    return type(error).__name__


def _save_image_atomically(
    image: Image.Image,
    output_path: Path,
    config: CollectorConfig,
    state: _CollectionState,
) -> int:
    """Сохраняет изображение через временный файл и проверяет общий размер."""

    temporary_path = output_path.with_name(output_path.name + ".part")

    try:
        image.save(
            temporary_path,
            format="JPEG",
            quality=config.jpeg_quality,
            optimize=True,
        )

        file_size = temporary_path.stat().st_size
        max_dataset_bytes = config.max_dataset_size_mb * 1024 * 1024

        if state.saved_bytes + file_size > max_dataset_bytes:
            raise DatasetSizeLimitError(
                "достигнут лимит общего размера датасета"
            )

        os.replace(temporary_path, output_path)
        state.saved_bytes += file_size
        return file_size

    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _collect_class(
    dataset_root: Path,
    class_name: str,
    queries: list[str],
    session,
    known_hashes: list[int],
    config: CollectorConfig,
    state: _CollectionState,
) -> None:
    """Собирает изображения для одной пользовательской папки-класса."""

    class_dir = dataset_root / "raw" / class_name
    metadata_path = dataset_root / "metadata.csv"

    ensure_child_path(dataset_root, class_dir)

    if class_dir.is_symlink():
        raise ValueError("папка класса не должна быть символической ссылкой")

    saved_count = len(
        [path for path in class_dir.glob("*.jpg") if not path.is_symlink()]
    )
    image_index = _next_image_index(class_dir)

    print(
        f"\n=== {class_name}: "
        f"{saved_count}/{config.target_images_per_class} ==="
    )

    for query in queries:
        if saved_count >= config.target_images_per_class:
            break

        if config.show_queries_in_console:
            print(f"\nПоиск: {query}")
        else:
            print("\nВыполняется следующий поисковый запрос")

        try:
            results = _search_images(query, config)
        except Exception as error:
            print(
                "Ошибка поиска: "
                f"{_safe_error_text(error, config.show_detailed_errors)}"
            )
            continue

        for result in results or []:
            if saved_count >= config.target_images_per_class:
                break

            if not isinstance(result, dict):
                continue

            image_url = result.get("image")

            if not isinstance(image_url, str) or not image_url:
                continue

            try:
                image, original_width, original_height = download_image(
                    session=session,
                    image_url=image_url,
                    config=config,
                )

                try:
                    image_hash = difference_hash(
                        image,
                        hash_size=config.hash_size,
                    )

                    if is_duplicate(
                        image_hash,
                        known_hashes,
                        config.duplicate_hash_distance,
                    ):
                        print("  дубликат — пропущен")
                        continue

                    filename = f"{image_index:04d}.jpg"
                    output_path = class_dir / filename
                    ensure_child_path(class_dir, output_path)

                    file_size = _save_image_atomically(
                        image=image,
                        output_path=output_path,
                        config=config,
                        state=state,
                    )
                finally:
                    image.close()

                try:
                    if config.write_metadata:
                        append_metadata(
                            metadata_path=metadata_path,
                            row={
                                "class_name": class_name,
                                "filename": filename,
                                "query": (
                                    query if config.store_search_queries else ""
                                ),
                                "title": result.get("title", ""),
                                "source_page": sanitize_url_for_metadata(
                                    str(result.get("url", "")),
                                    config.metadata_url_mode,
                                ),
                                "image_url": sanitize_url_for_metadata(
                                    image_url,
                                    config.metadata_url_mode,
                                ),
                                "search_source": result.get("source", ""),
                                "license_filter": config.license_image or "none",
                                "original_width": original_width,
                                "original_height": original_height,
                                "downloaded_at_utc": _utc_now(),
                            },
                            csv_formula_protection=config.csv_formula_protection,
                        )
                except Exception:
                    # Если строку метаданных сохранить не удалось, удаляем и
                    # изображение, чтобы файл и metadata.csv не расходились.
                    output_path.unlink(missing_ok=True)
                    state.saved_bytes = max(0, state.saved_bytes - file_size)
                    raise

                known_hashes.append(image_hash)
                saved_count += 1
                image_index += 1

                print(
                    f"  сохранено {saved_count}/"
                    f"{config.target_images_per_class}: "
                    f"{class_name}/{filename}"
                )

            except DatasetSizeLimitError:
                raise
            except (
                DownloadError,
                UnidentifiedImageError,
                Image.DecompressionBombError,
                OSError,
                ValueError,
            ) as error:
                print(
                    "  пропущено: "
                    f"{_safe_error_text(error, config.show_detailed_errors)}"
                )
            except Exception as error:
                print(
                    "  неожиданная ошибка: "
                    f"{_safe_error_text(error, config.show_detailed_errors)}"
                )

            time.sleep(config.download_delay_seconds)

        time.sleep(config.query_delay_seconds)

    print(f"Итог для {class_name}: {saved_count} изображений")


def collect_dataset(
    classes: dict[str, list[str]],
    output_dir: str | Path = "datasets",
    dataset_name: str = "dataset",
    config: CollectorConfig | None = None,
) -> Path:
    """Создаёт новый датасет по названиям папок и поисковым запросам."""

    normalized_classes = validate_classes(classes)
    safe_dataset_name = validate_path_component(
        dataset_name,
        field_name="название датасета",
    )

    config = config or CollectorConfig()
    config.validate()

    dataset_root = _create_dataset_root(
        output_dir=output_dir,
        class_names=list(normalized_classes),
        dataset_name=safe_dataset_name,
        config=config,
    )

    _write_config_snapshot(
        dataset_root=dataset_root,
        dataset_name=safe_dataset_name,
        classes=normalized_classes,
        config=config,
    )

    print(f"Создан новый датасет:\n{dataset_root}")

    session = create_http_session(config)  # Создает https сессию
    dataset_hashes: list[int] = []
    state = _CollectionState()

    try:
        for class_name, queries in normalized_classes.items(): # для каждого класса собирает изображения и скачивает
            if config.duplicate_scope == "dataset":
                known_hashes = dataset_hashes
            else:
                known_hashes = []

            try:
                _collect_class(
                    dataset_root=dataset_root,
                    class_name=class_name,
                    queries=queries,
                    session=session,
                    known_hashes=known_hashes,
                    config=config,
                    state=state,
                )
            except DatasetSizeLimitError as error:
                print(f"\nСбор остановлен: {error}")
                break
    finally:
        session.close()

    print("\nСбор завершён.")
    print(f"Проверь изображения в:\n{dataset_root / 'raw'}")

    return dataset_root
