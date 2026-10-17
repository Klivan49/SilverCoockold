"""Тесты внешнего healthcheck без сети."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.healthcheck import Healthcheck
from app.storage import Storage


class FakeScheduler:
    """Минимум полей, которые использует Healthcheck."""

    def __init__(self, storage: Storage, tracking: bool = True) -> None:
        self.storage = storage
        self.tracking = tracking


def make_storage(tmp_path: Path, with_poll: bool = True) -> Storage:
    """Storage с успешным опросом или без него."""
    storage = Storage(tmp_path / "test.db")
    if with_poll:
        storage.set_last_poll("q", datetime.now(timezone.utc).isoformat())
    return storage


def test_no_url_is_noop(tmp_path: Path) -> None:
    """Без URL пингер ничего не делает."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pinger = Healthcheck(None, FakeScheduler(make_storage(tmp_path)), client)
    try:
        assert asyncio.run(pinger.ping_once()) is False
    finally:
        asyncio.run(client.aclose())
    assert calls["n"] == 0


def test_ping_when_tracking_and_successful_poll(tmp_path: Path) -> None:
    """URL вызывается при tracking=true и наличии last_poll."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert str(request.url) == "https://hc.example/ping"
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pinger = Healthcheck(
        "https://hc.example/ping",
        FakeScheduler(make_storage(tmp_path)),
        client,
    )
    try:
        assert asyncio.run(pinger.ping_once()) is True
    finally:
        asyncio.run(client.aclose())
    assert calls["n"] == 1


def test_no_ping_without_tracking_or_poll(tmp_path: Path) -> None:
    """При tracking=false или без успешного опроса запросов нет."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        no_tracking = Healthcheck(
            "https://hc.example/ping",
            FakeScheduler(make_storage(tmp_path), tracking=False),
            client,
        )
        no_poll = Healthcheck(
            "https://hc.example/ping",
            FakeScheduler(
                make_storage(tmp_path / "no_poll", with_poll=False)
            ),
            client,
        )
        assert asyncio.run(no_tracking.ping_once()) is False
        assert asyncio.run(no_poll.ping_once()) is False
    finally:
        asyncio.run(client.aclose())
    assert calls["n"] == 0


def test_error_does_not_raise(tmp_path: Path) -> None:
    """Сетевая ошибка только логируется."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pinger = Healthcheck(
        "https://hc.example/ping",
        FakeScheduler(make_storage(tmp_path)),
        client,
    )
    try:
        assert asyncio.run(pinger.ping_once()) is False
    finally:
        asyncio.run(client.aclose())
