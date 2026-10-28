"""Тесты vision-анализатора без сети."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.analyzers.base import AiVerdict
from app.analyzers.vision import (
    NullVisionAnalyzer,
    VisionAnalyzer,
    VisionVerdict,
)
from app.models import Ad


def make_ad() -> Ad:
    """Объявление для vision."""
    return Ad(
        ad_id="1",
        subject="Браслет серебряный",
        price_byn=3000,
        url="https://www.kufar.by/item/1",
        list_time=None,
        raw={"category": "украшения", "region": "Минск"},
    )


def vision_content(**overrides: object) -> str:
    """JSON ответа vision."""
    data: dict[str, object] = {
        "is_silver": True,
        "item_type": "украшение",
        "weight_g": 10,
        "purity": 925,
        "hallmark": "зубр",
        "scale_visible": True,
        "stones_or_costume": False,
        "confidence": 0.9,
        "notes": "На фото клеймо и весы.",
        "question": "Подтвердите вес.",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


IMAGE = [("image/jpeg", "ZmFrZQ==")]


def make_analyzer(handler) -> tuple[VisionAnalyzer, httpx.AsyncClient]:
    """VisionAnalyzer с MockTransport."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        VisionAnalyzer(
            base_url="https://vision.example/v1",
            api_key="secret",
            model="claude-haiku-5-5",
            timeout=5,
            max_images=3,
            client=client,
        ),
        client,
    )


def response_with(content: str) -> httpx.Response:
    """Anthropic-подобный ответ."""
    return httpx.Response(
        200,
        json={"content": [{"type": "text", "text": content}]},
    )


def test_vision_parses_verdict_and_payload() -> None:
    """Корректный JSON, base64 и заголовки."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["payload"] = json.loads(request.content)
        return response_with(vision_content())

    analyzer, client = make_analyzer(handler)
    try:
        verdict = asyncio.run(
            analyzer.analyze(make_ad(), "описание", IMAGE)
        )
    finally:
        asyncio.run(client.aclose())

    assert verdict is not None
    assert verdict.weight_g == 10.0
    assert verdict.purity == 925
    assert verdict.hallmark == "зубр"
    assert verdict.scale_visible is True
    assert verdict.confidence == pytest.approx(0.9)
    assert seen["url"] == "https://vision.example/v1/messages"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["x-opencode-session"] == "ses_silverwatch_ai"
    payload = seen["payload"]
    assert payload["model"] == "claude-haiku-5-5"
    assert payload["max_tokens"] == 1200
    image = payload["messages"][0]["content"][1]
    assert image["source"]["type"] == "base64"
    assert image["source"]["media_type"] == "image/jpeg"


def test_vision_extracts_json_from_markdown_and_clamps() -> None:
    """JSON в markdown, неверные диапазоны исправляются."""
    wrapped = (
        "Вот результат:\n```json\n"
        + vision_content(weight_g=2000, purity=123, confidence=3)
        + "\n```"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return response_with(wrapped)

    analyzer, client = make_analyzer(handler)
    try:
        verdict = asyncio.run(analyzer.analyze(make_ad(), "", IMAGE))
    finally:
        asyncio.run(client.aclose())

    assert verdict is not None
    assert verdict.weight_g is None
    assert verdict.purity is None
    assert verdict.confidence == 1.0


def test_vision_broken_json_returns_none() -> None:
    """Битый JSON → None."""

    def handler(request: httpx.Request) -> httpx.Response:
        return response_with("not json")

    analyzer, client = make_analyzer(handler)
    try:
        assert asyncio.run(analyzer.analyze(make_ad(), "", IMAGE)) is None
    finally:
        asyncio.run(client.aclose())


def test_vision_429_retries_once() -> None:
    """429 повторяется один раз."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return response_with(vision_content())

    analyzer, client = make_analyzer(handler)
    try:
        verdict = asyncio.run(analyzer.analyze(make_ad(), "", IMAGE))
    finally:
        asyncio.run(client.aclose())

    assert calls["n"] == 2
    assert verdict is not None


def test_vision_timeout_returns_none() -> None:
    """Таймаут → None."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    analyzer, client = make_analyzer(handler)
    try:
        assert asyncio.run(analyzer.analyze(make_ad(), "", IMAGE)) is None
    finally:
        asyncio.run(client.aclose())


def test_vision_missing_key_or_images_returns_none() -> None:
    """Без ключа или картинок запрос не нужен."""
    ad = make_ad()
    assert asyncio.run(VisionAnalyzer(api_key="").analyze(ad, "", IMAGE)) is None
    assert asyncio.run(VisionAnalyzer(api_key="key").analyze(ad, "", [])) is None


def test_build_vision_analyzer_none_and_key() -> None:
    """Фабрика: none/без ключа → Null, anthropic+ключ → VisionAnalyzer."""
    none = SimpleNamespace(
        vision_provider="none",
        vision_api_key=None,
        vision_base_url="https://vision.example/v1",
        vision_model="claude-haiku-5-5",
        vision_timeout=60,
        vision_max_images=3,
        llm_session_id="ses_silverwatch_ai",
    )
    no_key = SimpleNamespace(
        vision_provider="anthropic",
        vision_api_key=None,
        vision_base_url="https://vision.example/v1",
        vision_model="claude-haiku-5-5",
        vision_timeout=60,
        vision_max_images=3,
        llm_session_id="ses_silverwatch_ai",
    )
    with_key = SimpleNamespace(
        vision_provider="anthropic",
        vision_api_key="key",
        vision_base_url="https://vision.example/v1",
        vision_model="claude-haiku-5-5",
        vision_timeout=60,
        vision_max_images=3,
        llm_session_id="ses_silverwatch_ai",
    )
    from app.analyzers.factory import build_vision_analyzer

    assert isinstance(build_vision_analyzer(none), NullVisionAnalyzer)
    assert isinstance(build_vision_analyzer(no_key), NullVisionAnalyzer)
    assert isinstance(build_vision_analyzer(with_key), VisionAnalyzer)
