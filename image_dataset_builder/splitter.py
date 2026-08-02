"""делит данные на выборки"""

from __future__ import annotations

import csv
import json
import random
import shutil
from pathlib import Path

from .collector import DATASET_MARKER_NAME
from .metadata import protect_csv_value, sync_metadata_with_raw
from .validation import ensure_child_path, validate_path_component


def _resolve_dataset_root(dataset_root: str | Path) -> Path:
    """Находит папку конкретного датасета по точному или родительскому пути."""

    requested_path = Path(dataset_root).expanduser()

    if not requested_path.exists():
        raise FileNotFoundError(
            f"указанный путь не существует: {requested_path.resolve(strict=False)}"
        )

    if requested_path.is_symlink():
        raise ValueError("dataset_root не должен быть символической ссылкой")

    resolved_path = requested_path.resolve(strict=True)

    if (resolved_path / DATASET_MARKER_NAME).is_file():
        return resolved_path

    # Пользователь часто указывает общую папку, внутри которой находится
    # единственная папка запуска с временной меткой. Разрешаем такой вариант.
    candidates = sorted(
        child
        for child in resolved_path.iterdir()
        if child.is_dir()
        and not child.is_symlink()
        and (child / DATASET_MARKER_NAME).is_file()
    )

    if len(candidates) == 1:
        selected = candidates[0].resolve(strict=True)
        print(f"Найден датасет: {selected}")
        return selected

    if len(candidates) > 1:
        candidate_names = ", ".join(path.name for path in candidates[:10])
        suffix = " ..." if len(candidates) > 10 else ""
        raise ValueError(
            "в указанной папке найдено несколько датасетов: "
            f"{candidate_names}{suffix}. Укажи путь к нужной папке запуска"
        )

    raise FileNotFoundError(
        f"в папке {resolved_path} не найден {DATASET_MARKER_NAME}. "
        "Укажи папку конкретного датасета, внутри которой находятся "
        "raw, train, test и служебный файл, либо её родительскую папку, "
        "если в ней только один датасет"
    )


def _load_marker(dataset_root: Path) -> dict:
    marker_path = dataset_root / DATASET_MARKER_NAME

    if marker_path.exists() and marker_path.stat().st_size > 1_000_000:
        raise ValueError("служебный файл датасета слишком большой")

    if not marker_path.is_file():
        raise FileNotFoundError(
            "не найден служебный файл датасета; удаление train/test отменено"
        )

    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("служебный файл датасета повреждён") from error

    if marker.get("format_version") != 1:
        raise ValueError("неподдерживаемая версия служебного файла датасета")

    return marker


def _clear_generated_folder(dataset_root: Path, folder: Path) -> None:
    """Удаляет только проверенную папку train или test внутри датасета."""

    ensure_child_path(dataset_root, folder)

    if folder.name not in {"train", "test"}:
        raise ValueError("разрешено очищать только папки train и test")

    if folder.is_symlink():
        raise ValueError("train/test не должны быть символическими ссылками")

    if folder.exists():
        shutil.rmtree(folder)

    folder.mkdir(parents=True, exist_ok=False)


def split_dataset(
    dataset_root: str | Path,
    class_names: list[str] | tuple[str, ...] | None = None,
    train_ratio: float = 0.80,
    random_seed: int = 42, # для воспроизводимости 
    overwrite_existing: bool = False,
) -> None:
    """Разделяет проверенную папку raw на train и test."""

    dataset_path = _resolve_dataset_root(dataset_root)
    marker = _load_marker(dataset_path)

    if not 0 < train_ratio < 1:
        raise ValueError("train_ratio должен быть между 0 и 1")

    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise TypeError("random_seed должен быть целым числом")

    if class_names is None:
        raw_class_names = marker.get("class_names", [])
    else:
        raw_class_names = list(class_names)

    if not raw_class_names:
        raise ValueError("не найдены названия папок-классов")

    if len(raw_class_names) > 1000:
        raise ValueError("разрешено не более 1000 папок-классов")

    safe_class_names = [
        validate_path_component(name, "название папки-класса")
        for name in raw_class_names
    ]

    raw_dir = dataset_path / "raw"

    if not raw_dir.is_dir() or raw_dir.is_symlink():
        raise FileNotFoundError(f"не найдена безопасная папка raw: {raw_dir}")

    # Пользователь мог вручную удалить или перенести плохие изображения из raw.
    # Перед разделением удаляем устаревшие строки из metadata.csv.
    removed_metadata_rows = sync_metadata_with_raw(dataset_path)
    if removed_metadata_rows:
        print(
            f"Метаданные синхронизированы: удалено строк — "
            f"{removed_metadata_rows}"
        )

    train_dir = dataset_path / "train"
    test_dir = dataset_path / "test"

    for folder in (train_dir, test_dir):
        if folder.is_symlink():
            raise ValueError("train/test не должны быть символическими ссылками")

    existing_content = any(
        folder.exists()
        and any(path.is_file() for path in folder.rglob("*"))
        for folder in (train_dir, test_dir)
    )

    if existing_content and not overwrite_existing:
        raise FileExistsError(
            "train/test уже содержат файлы; укажи overwrite_existing=True "
            "только после проверки пути"
        )

    _clear_generated_folder(dataset_path, train_dir)
    _clear_generated_folder(dataset_path, test_dir)

    random_generator = random.Random(random_seed)
    split_rows: list[dict[str, str]] = []

    print(f"\nРазделение датасета:\n{dataset_path}")

    for class_name in safe_class_names:
        source_dir = raw_dir / class_name
        train_class_dir = train_dir / class_name
        test_class_dir = test_dir / class_name

        ensure_child_path(dataset_path, source_dir)

        if not source_dir.is_dir() or source_dir.is_symlink():
            print(f"{class_name}: безопасная исходная папка не найдена")
            continue

        train_class_dir.mkdir(parents=True, exist_ok=False)
        test_class_dir.mkdir(parents=True, exist_ok=False)

        images = sorted(
            path
            for path in source_dir.glob("*.jpg")
            if path.is_file() and not path.is_symlink()
        )

        if len(images) < 2:
            print(
                f"{class_name}: недостаточно изображений "
                f"для разделения ({len(images)})"
            )
            continue

        random_generator.shuffle(images)

        train_count = max(1, int(len(images) * train_ratio))

        if train_count >= len(images):
            train_count = len(images) - 1

        train_images = images[:train_count]
        test_images = images[train_count:]

        for split_name, image_paths, destination in (
            ("train", train_images, train_class_dir),
            ("test", test_images, test_class_dir),
        ):
            for image_path in image_paths:
                destination_path = destination / image_path.name
                ensure_child_path(dataset_path, destination_path)

                # copyfile не переносит EXIF или системные метаданные файла.
                shutil.copyfile(image_path, destination_path)

                split_rows.append(
                    {
                        "class_name": protect_csv_value(class_name),
                        "filename": protect_csv_value(image_path.name),
                        "split": split_name,
                    }
                )

        print(
            f"{class_name}: train={len(train_images)}, "
            f"test={len(test_images)}"
        )

    split_path = dataset_path / "split.csv"

    with split_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["class_name", "filename", "split"],
        )
        writer.writeheader()
        writer.writerows(split_rows)

    print("\nРазделение завершено.")
    print(f"Train: {train_dir}")
    print(f"Test:  {test_dir}")
    print(f"Описание разделения: {split_path}")
