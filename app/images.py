"""Выбор и загрузка фото объявления для vision-анализа."""

from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Iterable

import httpx

from app.kufar import AdDetail

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ImageError(Exception):
    """Ошибка загрузки/формата изображения."""


def pick_images(detail: AdDetail, max_images: int = 3) -> list[str]:
    """Взять сначала gallery, затем listings, без дублей."""
    result: list[str] = []
    for url in [*detail.gallery, *detail.listings]:
        if url.startswith("http") and url not in result:
            result.append(url)
        if len(result) >= max_images:
            break
    return result


def _mime_type(response: httpx.Response, url: str) -> str | None:
    """Определить MIME изображения из заголовка или расширения."""
    content_type = (
        response.headers.get("content-type", "").split(";")[0].strip().lower()
    )
    if content_type:
        return content_type if content_type.startswith("image/") else None
    guessed, _ = mimetypes.guess_type(url)
    if guessed and guessed.startswith("image/"):
        return guessed
    return None


def download_images(
    urls: Iterable[str],
    timeout: float = 30.0,
    max_bytes: int = MAX_IMAGE_BYTES,
    client: httpx.Client | None = None,
) -> list[tuple[str, str]]:
    """Скачать изображения и вернуть [(media_type, base64)], без записи на диск."""
    own_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=True)
    result: list[tuple[str, str]] = []
    try:
        for url in urls:
            try:
                response = http.get(url)
            except httpx.HTTPError as exc:
                logger.warning("images: не удалось скачать %s: %s", url, type(exc).__name__)
                continue
            if response.status_code != 200:
                logger.warning("images: HTTP %d для %s", response.status_code, url)
                continue

            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                logger.warning("images: %s больше %d байт", url, max_bytes)
                continue

            mime = _mime_type(response, url)
            if mime is None:
                logger.warning("images: неизвестный MIME для %s", url)
                continue
            data = response.content
            if not data or len(data) > max_bytes:
                logger.warning("images: пустой или слишком большой файл %s", url)
                continue
            result.append((mime, base64.b64encode(data).decode("ascii")))
    finally:
        if own_client:
            http.close()
    return result
