"""Тесты клиента Kufar. Сеть не используется."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.kufar import KufarClient, KufarError

FIXTURE = Path(__file__).parent / "fixtures" / "search_lom.json"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "detail_sample.html"


def load_fixture() -> dict:
    """Прочитать сохранённый ответ Kufar."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_client(handler, sleeps: list[float] | None = None) -> KufarClient:
    """Собрать клиент на MockTransport с записью пауз."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, timeout=1.0)
    return KufarClient(
        client=http_client,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


def detail_html() -> str:
    """Прочитать HTML detail-страницы из фикстуры."""
    return DETAIL_FIXTURE.read_text(encoding="utf-8")


def test_fetch_detail_parses_next_data() -> None:
    """fetch_detail достаёт body из __NEXT_DATA__."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "text/html,application/xhtml+xml"
        return httpx.Response(200, text=detail_html())

    body = make_client(handler).fetch_detail("https://www.kufar.by/item/1")

    assert body.startswith("Серебро 925 проба.")
    assert "Общий вес 15 г." in body


def test_fetch_detail_retries_429() -> None:
    """Detail-запрос повторяется на 429 с паузами."""
    sleeps: list[float] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429)
        return httpx.Response(200, text=detail_html())

    body = make_client(handler, sleeps).fetch_detail("https://www.kufar.by/item/1")

    assert body.startswith("Серебро")
    assert calls["n"] == 3
    assert sleeps == [30, 60]


def test_fetch_detail_broken_next_data() -> None:
    """Битый JSON внутри __NEXT_DATA__ даёт KufarError."""

    html = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        "{not json}</script></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    with pytest.raises(KufarError) as caught:
        make_client(handler).fetch_detail("https://www.kufar.by/item/1")

    assert caught.value.kind == "parse"


def test_fetch_detail_bad_structure() -> None:
    """Неверная структура data тоже даёт KufarError."""
    html = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"initialState":{"adView":{"data":"oops"}}}}'
        "</script></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    with pytest.raises(KufarError):
        make_client(handler).fetch_detail("https://www.kufar.by/item/1")


def test_fetch_parses_fixture() -> None:
    payload = load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    ads = make_client(handler).fetch("серебро лом")

    assert len(ads) == len(payload["ads"])
    assert ads[0].ad_id == "1089730241"
    assert ads[0].subject == "монеты серебро"
    assert ads[0].price_byn == 2499
    assert ads[0].url.startswith("https://www.kufar.by/")
    assert ads[0].raw["ad_id"] == 1089730241


def test_request_params() -> None:
    seen: dict[str, httpx.URL] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        return httpx.Response(200, json={"ads": [], "total": 0})

    make_client(handler).fetch("серебро лом")
    params = dict(seen["url"].params)

    assert params["query"] == "серебро лом"
    assert params["size"] == "100"
    assert "token" not in params


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2499", 2499),
        (2499, 2499),
        ("0", None),
        (0, None),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_price(value, expected) -> None:
    assert KufarClient._parse_price(value) == expected


def test_retry_on_429_then_success() -> None:
    payload = load_fixture()
    sleeps: list[float] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=payload)

    ads = make_client(handler, sleeps).fetch("серебро лом")

    assert len(ads) == len(payload["ads"])
    assert sleeps == [30]
    assert calls["n"] == 2


def test_retry_after_header_wins() -> None:
    sleeps: list[float] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json={"ads": [], "total": 0})

    make_client(handler, sleeps).fetch("q")

    assert sleeps == [5]


def test_429_exhausts_retries() -> None:
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with pytest.raises(KufarError) as caught:
        make_client(handler, sleeps).fetch("q")

    assert caught.value.kind == "rate_limit"
    assert caught.value.status_code == 429
    assert sleeps == [30, 60, 120]


def test_500_exhausts_retries() -> None:
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(KufarError) as caught:
        make_client(handler, sleeps).fetch("q")

    assert caught.value.kind == "http"
    assert caught.value.status_code == 500
    assert sleeps == [30, 60, 120]


def test_404_fails_immediately() -> None:
    sleeps: list[float] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(KufarError) as caught:
        make_client(handler, sleeps).fetch("q")

    assert caught.value.kind == "http"
    assert caught.value.status_code == 404
    assert calls["n"] == 1
    assert sleeps == []


def test_broken_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(KufarError) as caught:
        make_client(handler).fetch("q")

    assert caught.value.kind == "parse"


def test_missing_ads_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0})

    with pytest.raises(KufarError) as caught:
        make_client(handler).fetch("q")

    assert caught.value.kind == "parse"


def test_network_error_kind() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(KufarError) as caught:
        make_client(handler).fetch("q")

    assert caught.value.kind == "network"
    assert caught.value.status_code is None


def test_ad_without_link_is_skipped() -> None:
    payload = {
        "ads": [
            {"ad_id": 1, "subject": "без ссылки", "price_byn": "10"},
            {
                "ad_id": 2,
                "subject": "нормальное",
                "ad_link": "https://www.kufar.by/item/2",
                "price_byn": "20",
            },
        ],
        "total": 2,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    ads = make_client(handler).fetch("q")

    assert len(ads) == 1
    assert ads[0].ad_id == "2"
    assert ads[0].price_byn == 20
