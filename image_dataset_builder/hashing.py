"""глядя на вектор изображения, определяет схожесть нового изображения с тем, что было добавлено в датасет ранее"""


from __future__ import annotations

from PIL import Image, ImageOps


def difference_hash(image: Image.Image, hash_size: int = 8) -> int: # использует метод хэширования - Difference Hash(через сравнение соседних пикселей в векторе)
    """Создаёт визуальный отпечаток изображения методом dHash."""

    gray = ImageOps.grayscale(image)  # перевод изображения в черно-белое
    resized = gray.resize( # уменьшение изображения
        (hash_size + 1, hash_size),
        Image.Resampling.LANCZOS,
    )
    pixels = list(resized.getdata())

    value = 0

    for row in range(hash_size):
        row_start = row * (hash_size + 1)

        for column in range(hash_size): # использует сравнение яркости соседних пикселей
            left = pixels[row_start + column]
            right = pixels[row_start + column + 1]
            value = (value << 1) | int(left > right)

    return value


def is_duplicate( # для определения степени различия векторов используется расстояние Хэмминга, для удаления дубликатов можно без embeddings
    new_hash: int,
    known_hashes: list[int],
    max_distance: int,
) -> bool:
    """Сравнивает новый отпечаток с уже сохранёнными изображениями."""

    return any(
        (new_hash ^ old_hash).bit_count() <= max_distance
        for old_hash in known_hashes
    )
