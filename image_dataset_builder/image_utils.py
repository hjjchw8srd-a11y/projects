"""обработка полученного из downloader.py изображения"""
from __future__ import annotations

import io
import warnings

from PIL import Image, ImageOps

from .config import CollectorConfig


def prepare_image(
    content: bytes,
    config: CollectorConfig,
) -> tuple[Image.Image, int, int]:
    """Проверяет изображение, приводит его к RGB и удаляет метаданные."""

    source_image: Image.Image | None = None

    try:
        # DecompressionBombWarning превращается в исключение, чтобы маленький
        # сжатый файл не смог развернуться в гигантское изображение в памяти.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)

            source_image = Image.open( # открывает файл ил байтов
                io.BytesIO(content),
                formats=list(config.allowed_image_formats),
            )

            original_width, original_height = source_image.size
            pixel_count = original_width * original_height

            if pixel_count > config.max_image_pixels:
                raise ValueError(
                    "изображение содержит слишком много пикселей: "
                    f"{pixel_count}"
                )

            if (
                not config.allow_animated_images
                and getattr(source_image, "n_frames", 1) > 1
            ):
                raise ValueError("анимированные изображения запрещены")

            source_image.load()

        if (
            original_width < config.min_width
            or original_height < config.min_height
        ):
            raise ValueError(
                "слишком маленькое изображение: "
                f"{original_width}x{original_height}"
            )

        # Исправляем ориентацию по EXIF до удаления метаданных(EXIF - доп. данные внутри фотографии.
        image = ImageOps.exif_transpose(source_image)

        # Палитровые PNG/GIF могут хранить прозрачность отдельно от каналов.
        # Сначала переводим их в RGBA, иначе Pillow выводит предупреждение и
        # прозрачные области могут преобразоваться некорректно.
        if image.mode == "P" and "transparency" in image.info:
            image = image.convert("RGBA")

        # Если есть прозрачность, заменяем её белым фоном перед сохранением JPEG.
        if image.mode not in ("RGB", "L"):
            if "A" in image.getbands():
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
        else:
            image = image.convert("RGB")

        # Уменьшаем слишком большие изображения с сохранением пропорций.
        if max(image.size) > config.max_side:
            image.thumbnail(
                (config.max_side, config.max_side),
                Image.Resampling.LANCZOS,
            )

        # Не переносим EXIF, GPS, комментарии и другие данные исходного файла.
        image.info.clear()

        return image, original_width, original_height

    finally:
        if source_image is not None:
            source_image.close()
