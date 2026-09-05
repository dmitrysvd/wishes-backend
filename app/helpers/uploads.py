from typing import Any

from fastapi import HTTPException, UploadFile
from starlette.status import (
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
)

from app.constants import UPLOAD_IMAGE_MAX_BYTES
from app.helpers.user_helpers import guess_image_extension

# Коды ответов загрузки картинки — для `responses={...}` эндпоинтов (контракт).
IMAGE_UPLOAD_RESPONSES: dict[int | str, dict[str, Any]] = {
    413: {
        'description': (
            f'Файл больше {UPLOAD_IMAGE_MAX_BYTES // (1024 * 1024)} МБ. '
            'Клиенту стоит сжать картинку и повторить.'
        ),
        'content': {'application/json': {'example': {'detail': 'Файл больше 10 МБ'}}},
    },
    415: {
        'description': (
            'Содержимое не распознано как картинка (по сигнатуре байт, а не по '
            'имени файла или Content-Type). Принимаются PNG, JPEG, GIF, WebP.'
        ),
        'content': {
            'application/json': {'example': {'detail': 'Файл не является картинкой'}}
        },
    },
}


def read_uploaded_image(file: UploadFile) -> tuple[bytes, str]:
    """Прочитать загруженную картинку в память, проверив размер и тип.

    Порядок важен: размер сверяем по `file.size` ДО `read()` — Starlette считает
    его по фактически принятым байтам (spool на диске), так что переразмерное
    тело не попадает в RAM. Тип определяем по сигнатуре: клиент не шлёт
    Content-Type у multipart-части, а имя файла может врать (после кропа —
    PNG с именем `.jpg`). Возвращает байты и расширение с точкой.
    """
    if file.size is not None and file.size > UPLOAD_IMAGE_MAX_BYTES:
        raise HTTPException(
            HTTP_413_CONTENT_TOO_LARGE,
            f'Файл больше {UPLOAD_IMAGE_MAX_BYTES // (1024 * 1024)} МБ',
        )
    content = file.file.read()
    extension = guess_image_extension(content)
    if not extension:
        raise HTTPException(
            HTTP_415_UNSUPPORTED_MEDIA_TYPE, 'Файл не является картинкой'
        )
    return content, extension
