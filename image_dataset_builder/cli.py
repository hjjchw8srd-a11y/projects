"""собирает информацию о пользовательских настройках сбора изображений через терминал"""


from __future__ import annotations

import argparse # для работы с командной строкой
from pathlib import Path # для удобной работы с путями
from typing import Callable, TypeVar

from .collector import collect_dataset
from .config import CollectorConfig # хранит все параметры парсера
from .splitter import split_dataset # импорт ф-ии разделения датасета
from .validation import validate_path_component, validate_query # импорт ф-ий для проверки ввода пользователя


T = TypeVar("T")


_LICENSE_CHOICES = {
    "any": "any",
    "public": "Public",
    "share": "Share",
    "commercial": "ShareCommercially",
    "modify": "Modify",
    "commercial_modify": "ModifyCommercially",
}


def _ask_text(prompt: str, default: str | None = None) -> str: # запрашивает текстовое значение
    while True:
        suffix = f" [{default}]" if default is not None else ""
        value = input(f"{prompt}{suffix}: ").strip()

        if value:
            return value

        if default is not None:
            return default

        print("Значение не должно быть пустым.")


def _ask_value(    # запрашивает числовое или другое значение с проверкой(например, кол-во изображений)
    prompt: str,
    default: T,
    converter: Callable[[str], T],
    validator: Callable[[T], bool] | None = None,
    range_hint: str | None = None,
) -> T:
    while True:
        raw_value = input(f"{prompt} [{default}]: ").strip()

        if not raw_value:
            return default

        try:
            value = converter(raw_value)
        except (TypeError, ValueError):
            print("Некорректное значение. Попробуй ещё раз.")
            continue

        if validator is not None and not validator(value):
            if range_hint:
                print(f"Допустимое значение: {range_hint}.")
            else:
                print("Значение находится вне допустимого диапазона.")
            continue

        return value


def _ask_bool(prompt: str, default: bool) -> bool:
    default_text = "Y/n" if default else "y/N"

    while True:
        value = input(f"{prompt} [{default_text}]: ").strip().lower()

        if not value:
            return default

        if value in {"y", "yes", "ye", "да", "д"}:
            return True

        if value in {"n", "no", "нет", "н"}:
            return False

        print("Введи да или нет.")


def _ask_choice(prompt: str, choices: tuple[str, ...], default: str) -> str:  # позволяет выбрать вариант из списка
    choices_text = "/".join(choices)
    normalized_choices = {choice.casefold(): choice for choice in choices}

    while True:
        value = input(f"{prompt} ({choices_text}) [{default}]: ").strip()
        selected = value or default
        normalized = normalized_choices.get(selected.casefold())

        if normalized is not None:
            return normalized

        print("Выбери одно из перечисленных значений.")


def _input_classes() -> dict[str, list[str]]: # создает список классов будущего датасета
    classes: dict[str, list[str]] = {}

    class_count = _ask_value(
        "Количество папок-классов",
        default=1,
        converter=int,
        validator=lambda value: 1 <= value <= 100,
        range_hint="целое число от 1 до 100",
    )

    for class_number in range(1, class_count + 1):
        while True:
            raw_name = _ask_text(f"Название папки №{class_number}")

            try:
                class_name = validate_path_component(
                    raw_name,
                    field_name="название папки",
                )
            except (TypeError, ValueError) as error:
                print(f"Ошибка: {error}")
                continue

            if class_name.casefold() in {
                existing.casefold() for existing in classes
            }:
                print("Такая папка уже добавлена.")
                continue

            break

        queries: list[str] = []
        print(
            "Вводи поисковые запросы по одному. "
            "Пустая строка завершает список."
        )

        while True:
            raw_query = input(
                f"Запрос для папки {class_name!r} "
                f"№{len(queries) + 1}: "
            )

            if not raw_query.strip():
                if queries:
                    break

                print("Нужен хотя бы один запрос.")
                continue

            try:
                queries.append(validate_query(raw_query))
            except (TypeError, ValueError) as error:
                print(f"Ошибка: {error}")

        classes[class_name] = queries

    return classes


def _input_config(full_settings: bool) -> CollectorConfig:  # создает объект настроек парсера
    config = CollectorConfig()

    print("\nОсновные параметры парсера")

    config.target_images_per_class = _ask_value(
        "Изображений для каждой папки",
        config.target_images_per_class,
        int,
        lambda value: 1 <= value <= 10_000,
        "целое число от 1 до 10000",
    )
    config.max_results_per_query = _ask_value(
        "Максимальное число найденных ссылок для одного поискового запроса",
        config.max_results_per_query,
        int,
        lambda value: 1 <= value <= 1_000,
        "целое число от 1 до 1000",
    )
    config.min_width = _ask_value(
        "Минимальная ширина изображения",
        config.min_width,
        int,
        lambda value: 1 <= value <= 20_000,
        "целое число от 1 до 20000",
    )
    config.min_height = _ask_value(
        "Минимальная высота изображения",
        config.min_height,
        int,
        lambda value: 1 <= value <= 20_000,
        "целое число от 1 до 20000",
    )
    config.max_side = _ask_value(
        "Максимальная сторона после уменьшения",
        config.max_side,
        int,
        lambda value: max(config.min_width, config.min_height) <= value <= 20_000,
        "не меньше минимальной ширины/высоты и не больше 20000",
    )
    config.max_file_size_mb = _ask_value(
        "Максимальный размер одного файла, МБ",
        config.max_file_size_mb,
        int,
        lambda value: 1 <= value <= 1_000,
        "целое число от 1 до 1000",
    )
    config.max_dataset_size_mb = _ask_value(
        "Максимальный общий размер датасета, МБ",
        config.max_dataset_size_mb,
        int,
        lambda value: 1 <= value <= 1_000_000,
        "целое число от 1 до 1000000",
    )
    config.region = _ask_text("Регион поиска", config.region)

    print(
        "Безопасный поиск фильтрует нежелательный контент: "
        "off — выключен, moderate — умеренный, on — строгий."
    )
    config.safesearch = _ask_choice(
        "Режим безопасного поиска",
        ("off", "moderate", "on"),
        config.safesearch,
    )

    print(
        "Паузы уменьшают нагрузку на сайты и вероятность временной блокировки."
    )
    config.download_delay_seconds = _ask_value(
        "Пауза между загрузками, секунд",
        config.download_delay_seconds,
        float,
        lambda value: 0 <= value <= 60,
        "число от 0 до 60",
    )
    config.query_delay_seconds = _ask_value(
        "Пауза между поисковыми запросами, секунд",
        config.query_delay_seconds,
        float,
        lambda value: 0 <= value <= 300,
        "число от 0 до 300",
    )

    if full_settings:
        print("\nДополнительные параметры")

        print(
            "Качество JPEG: 1–40 — низкое, 50–75 — среднее, "
            "85–92 — рекомендуемое, 93–100 — большие файлы."
        )
        config.jpeg_quality = _ask_value(
            "Качество сохраняемого JPEG",
            config.jpeg_quality,
            int,
            lambda value: 1 <= value <= 100,
            "целое число от 1 до 100; рекомендуется 85–92",
        )

        print(
            "Период публикации результатов: all — без ограничения, "
            "d — день, w — неделя, m — месяц, y — год."
        )
        raw_timelimit = _ask_choice(
            "Период публикации результатов поиска",
            ("all", "d", "w", "m", "y"),
            config.search_timelimit or "all",
        )
        config.search_timelimit = (
            None if raw_timelimit == "all" else raw_timelimit
        )

        print(
            "Фильтр лицензии лишь ограничивает поисковую выдачу и не гарантирует "
            "право на публикацию датасета."
        )
        license_mode = _ask_choice(
            "Лицензия: any — без фильтра, public — общественное достояние, "
            "share — повторное использование, commercial — коммерческое, "
            "modify — изменение, commercial_modify — изменение и коммерческое",
            tuple(_LICENSE_CHOICES),
            "any",
        )
        config.license_image = _LICENSE_CHOICES[license_mode]

        print(
            "Чувствительность поиска дубликатов: "
            "strict — почти точные копии; normal — рекомендуемый режим; "
            "aggressive — удаляет больше похожих изображений и может ошибаться."
        )
        config.duplicate_mode = _ask_choice(
            "Режим поиска дубликатов",
            ("strict", "normal", "aggressive"),
            config.duplicate_mode,
        )

        print(
            "Область проверки: dataset — сравнивать со всем датасетом; "
            "class — только внутри текущего класса."
        )
        config.duplicate_scope = _ask_choice(
            "Область проверки дубликатов",
            ("dataset", "class"),
            config.duplicate_scope,
        )

        config.write_metadata = _ask_bool(
            "Записывать metadata.csv",
            config.write_metadata,
        )

        # Вопросы о CSV задаются только тогда, когда метаданные включены.
        if config.write_metadata:
            print(
                "Хранение URL: sanitized — без параметров запроса, "
                "full — полный URL, none — не сохранять URL."
            )
            config.metadata_url_mode = _ask_choice(
                "Режим хранения URL в метаданных",
                ("sanitized", "full", "none"),
                config.metadata_url_mode,
            )
            config.store_search_queries = _ask_bool(
                "Хранить поисковые запросы в metadata.csv",
                config.store_search_queries,
            )

        config.show_queries_in_console = _ask_bool(
            "Показывать поисковые запросы в терминале",
            config.show_queries_in_console,
        )

    # Технические и сетевые параметры намеренно не спрашиваются у пользователя.
    # Используются HTTPS, порт 443, блокировка локальных IP и проверка image/*.
    config.validate()
    return config


def run_interactive_collection() -> None: # реализует опрос пользователя
    print("\nСоздание нового датасета")
    print(
        "Важно: поисковые запросы будут отправлены внешней поисковой "
        "системе, а найденные изображения — скачаны с внешних сайтов."
    )

    output_dir = _ask_text("Папка для датасетов", "datasets")

    while True:
        try:
            dataset_name = validate_path_component(
                _ask_text("Название нового датасета", "dataset"),
                field_name="название датасета",
            )
            break
        except (TypeError, ValueError) as error:
            print(f"Ошибка: {error}")

    classes = _input_classes()
    full_settings = _ask_bool("Открыть дополнительные настройки", False)
    config = _input_config(full_settings=full_settings)

    print("\nИтоговая настройка")
    print(f"Папка: {output_dir}")
    print(f"Название датасета: {dataset_name}")
    print(f"Количество классов: {len(classes)}")
    print(
        "Максимум изображений: "
        f"{len(classes) * config.target_images_per_class}"
    )

    if not _ask_bool("Начать сбор", True):
        print("Сбор отменён.")
        return

    collect_dataset(
        classes=classes,
        output_dir=output_dir,
        dataset_name=dataset_name,
        config=config,
    )


def run_interactive_split() -> None:  # разделяет датасет на тестовую и тренировочную выборки
    print(
        "Укажи папку конкретного датасета, где находятся raw, train и test. "
        "Также можно указать родительскую папку, если внутри неё только "
        "один созданный датасет."
    )
    dataset_root = _ask_text("Путь к проверенному датасету")
    train_ratio = _ask_value(
        "Доля train",
        0.80,
        float,
        lambda value: 0 < value < 1,
        "число больше 0 и меньше 1",
    )
    random_seed = _ask_value("Random seed", 42, int)

    overwrite = _ask_bool(
        "Разрешить удалить содержимое существующих train/test",
        False,
    )

    split_dataset(
        dataset_root=dataset_root,
        train_ratio=train_ratio,
        random_seed=random_seed,
        overwrite_existing=overwrite,
    )


def interactive_menu() -> None: # связывает все режимы работы
    while True:
        print("\nImage Dataset Builder")
        print("1 — создать датасет через мастер настройки")
        print("2 — разделить проверенную папку raw на train и test")
        print("0 — выйти")

        choice = input("Выбор: ").strip()

        try:
            if choice == "1":
                run_interactive_collection()
            elif choice == "2":
                run_interactive_split()
            elif choice == "0":
                return
            else:
                print("Неизвестный пункт меню.")
        except KeyboardInterrupt:
            print("\nОперация отменена пользователем.")
        except PermissionError as error:
            print(f"Ошибка доступа: {error}")
        except (TypeError, ValueError, FileNotFoundError, FileExistsError) as error:
            print(f"Ошибка: {error}")
        except Exception as error:
            print(f"Неожиданная ошибка: {type(error).__name__}: {error}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Сбор изображений и создание структуры датасета.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "collect",
        help="запустить интерактивный сбор датасета",
    )

    split_parser = subparsers.add_parser(
        "split",
        help="разделить raw на train и test",
    )
    split_parser.add_argument("dataset_root", type=Path)
    split_parser.add_argument("--train-ratio", type=float, default=0.80)
    split_parser.add_argument("--seed", type=int, default=42)
    split_parser.add_argument("--overwrite", action="store_true")

    return parser


def main() -> None:
    parser = build_argument_parser()
    arguments = parser.parse_args()

    if arguments.command == "collect":
        run_interactive_collection()
    elif arguments.command == "split":
        split_dataset(
            dataset_root=arguments.dataset_root,
            train_ratio=arguments.train_ratio,
            random_seed=arguments.seed,
            overwrite_existing=arguments.overwrite,
        )
    else:
        interactive_menu()
