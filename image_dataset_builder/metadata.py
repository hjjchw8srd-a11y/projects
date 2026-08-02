"""Сохраняет метаданные о изображениях. Сохраняется: класс изображения, имя файла, поисковой запрос, url, размер исходного изображения, время загрузки"""



from __future__ import annotations

import csv
import os
import shutil
from pathlib import Path
from typing import Any

from .validation import ensure_child_path, validate_path_component


METADATA_FIELDS = [
    "class_name",
    "filename",
    "query",
    "title",
    "source_page",
    "image_url",
    "search_source",
    "license_filter",
    "original_width",
    "original_height",
    "downloaded_at_utc",
]

_FORMULA_PREFIXES = (
    "=",
    "+",
    "-",
    "@",
    "＝",
    "＋",
    "－",
    "＠",
)


def protect_csv_value(value: Any, enabled: bool = True) -> str:
    """Снижает риск выполнения формул при открытии CSV в таблицах."""

    text = "" if value is None else str(value)

    # Переносы строк не нужны в метаданных и усложняют ручную проверку CSV.
    text = text.replace("\r", " ").replace("\n", " ")

    if not enabled:
        return text

    stripped = text.lstrip(" \t")

    if stripped.startswith(_FORMULA_PREFIXES):
        # Табуляция заставляет Excel рассматривать поле как текст.
        return "\t" + text

    return text


def append_metadata(
    metadata_path: Path,
    row: dict[str, Any],
    csv_formula_protection: bool = True,
) -> None:
    """Добавляет одну строку в metadata.csv."""

    write_header = not metadata_path.exists() # проверяет наличие существования файла
    safe_row = {
        field: protect_csv_value(
            row.get(field, ""),
            enabled=csv_formula_protection,
        )
        for field in METADATA_FIELDS
    }

    with metadata_path.open(
        "a",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=METADATA_FIELDS)

        if write_header:
            writer.writeheader()

        writer.writerow(safe_row)


def sync_metadata_with_raw(dataset_root: str | Path) -> int:
    """Удаляет из metadata.csv записи о файлах, которых больше нет в raw."""

    dataset_path = Path(dataset_root)
    metadata_path = dataset_path / "metadata.csv"
    raw_dir = dataset_path / "raw"

    if not metadata_path.is_file():
        return 0

    if not raw_dir.is_dir():
        raise FileNotFoundError(f"не найдена папка raw: {raw_dir}")

    if metadata_path.stat().st_size > 100_000_000:
        raise ValueError("metadata.csv слишком большой для безопасной синхронизации")

    with metadata_path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames is None:
            raise ValueError("metadata.csv не содержит заголовок")

        missing_fields = {"class_name", "filename"} - set(reader.fieldnames)
        if missing_fields:
            raise ValueError("metadata.csv не содержит class_name или filename")

        rows = list(reader)

    kept_rows: list[dict[str, str]] = []

    for row in rows:
        try:
            class_name = validate_path_component(
                row.get("class_name", "").removeprefix("\t"),
                field_name="class_name в metadata.csv",
            )
            filename = validate_path_component(
                row.get("filename", "").removeprefix("\t"),
                field_name="filename в metadata.csv",
            )
        except (TypeError, ValueError):
            continue

        image_path = raw_dir / class_name / filename
        ensure_child_path(raw_dir, image_path)

        if image_path.is_file() and not image_path.is_symlink():
            kept_rows.append(
                {field: row.get(field, "") for field in METADATA_FIELDS}
            )

    removed_count = len(rows) - len(kept_rows)

    if removed_count == 0:
        return 0

    temporary_path = metadata_path.with_name(metadata_path.name + ".part")

    try:
        with temporary_path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(file, fieldnames=METADATA_FIELDS)
            writer.writeheader()
            writer.writerows(kept_rows)

        try:
            # Атомарная замена предпочтительна: старый CSV остаётся целым,
            # пока новый файл полностью не записан.
            os.replace(temporary_path, metadata_path)
        except PermissionError:
            # На Windows редактор или проводник иногда разрешает перезапись
            # открытого файла, но запрещает его удаление/замену. В таком случае
            # копируем уже готовый временный CSV поверх существующего файла.
            try:
                with temporary_path.open("rb") as source_file:
                    with metadata_path.open("wb") as destination_file:
                        shutil.copyfileobj(source_file, destination_file)
                        destination_file.flush()
                        os.fsync(destination_file.fileno())
            except PermissionError as error:
                raise PermissionError(
                    "не удалось обновить metadata.csv: файл открыт или "
                    "заблокирован другой программой. Закрой его в Excel, "
                    "PyCharm или проводнике и повтори разделение"
                ) from error
    finally:
        try:
            if temporary_path.exists():
                temporary_path.unlink()
        except OSError:
            # Временный файл не влияет на целостность датасета. Его можно
            # удалить вручную после закрытия программы, которая его блокирует.
            pass

    return removed_count
