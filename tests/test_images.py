"""Тесты выбора и загрузки фото без сети."""

from __future__ import annotations

import base64

import httpx
import pytest

from app.images import download_images, pick_images
from app.kufar import AdDetail


def test_pick_images_gallery_then_listings() -> None:
    """Сначала gallery, затем listings, без дублей и с лимитом."""
    detail = AdDetail(
        body="",
        gallery=["https://i/1.jpg", "https://i/2.jpg"],
        listings=["https://i/2.jpg", "https://i/3.jpg", "https://i/4.jpg"],
    )

    assert pick_images(detail, 3) == [
        "https://i/1.jpg",
        "https://i/2.jpg",
        "https://i/3.jpg",
    ]


def test_download_images() -> None:
    """Картинка скачивается в base64, без записи на диск."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"jpeg-bytes",
            headers={"content-type": "image/jpeg"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = download_images(["https://i/1.jpg"], client=client)
    client.close()

    assert result == [
        ("image/jpeg", base64.b64encode(b"jpeg-bytes").decode("ascii"))
    ]


def test_download_images_skips_bad_or_large() -> None:
    """HTTP-ошибки, не-картинки и большие файлы пропускаются."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("404.jpg"):
            return httpx.Response(404)
        if request.url.path.endswith("text.jpg"):
            return httpx.Response(200, content=b"text", headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            content=b"x" * 11,
            headers={"content-type": "image/png"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = download_images(
        [
            "https://i/404.jpg",
            "https://i/text.jpg",
            "https://i/big.png",
        ],
        max_bytes=10,
        client=client,
    )
    client.close()

    assert result == []


def test_download_images_network_error_does_not_raise() -> None:
    """Сетевая ошибка не роняет загрузку остальных."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("bad.jpg"):
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, content=b"ok", headers={"content-type": "image/png"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = download_images(
        ["https://i/bad.jpg", "https://i/good.png"], client=client
    )
    client.close()

    assert len(result) == 1
    assert result[0][0] == "image/png"
