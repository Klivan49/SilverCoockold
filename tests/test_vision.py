"""Тесты vision-анализатора без сети."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.analyzers.base import AiVerdict
from app.analyzers.vision import (
    DeepSeekVisionAnalyzer,
    FallbackVisionAnalyzer,
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


def openai_response_with(content: str) -> httpx.Response:
    """OpenAI-подобный ответ."""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


def make_deepseek(
    handler,
) -> tuple[DeepSeekVisionAnalyzer, httpx.AsyncClient]:
    """DeepSeekVisionAnalyzer с MockTransport."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        DeepSeekVisionAnalyzer(
            base_url="https://vision.example/v1",
            api_key="secret",
            model="deepseek-v4.1-flash",
            timeout=5,
            max_images=3,
            client=client,
        ),
        client,
    )


def test_deepseek_payload_headers_and_parsing() -> None:
    """DeepSeek: chat/completions, image_url с data URI, JSON-разбор."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["payload"] = json.loads(request.content)
        return openai_response_with(vision_content())

    analyzer, client = make_deepseek(handler)
    try:
        verdict = asyncio.run(analyzer.analyze(make_ad(), "описание", IMAGE))
    finally:
        asyncio.run(client.aclose())

    assert verdict is not None
    assert verdict.weight_g == 10.0
    assert verdict.purity == 925
    assert verdict.model == "deepseek-v4.1-flash"
    assert seen["url"] == "https://vision.example/v1/chat/completions"
    headers = seen["headers"]
    assert headers["authorization"] == "Bearer secret"
    assert headers["x-opencode-session"] == "ses_silverwatch_ai"
    assert "anthropic-version" not in headers
    payload = seen["payload"]
    assert payload["model"] == "deepseek-v4.1-flash"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 1200
    blocks = payload["messages"][0]["content"]
    assert blocks[0]["type"] == "text"
    assert "Верни СТРОГО JSON" in blocks[0]["text"]
    assert blocks[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,ZmFrZQ=="},
    }


def test_deepseek_retries_5xx_once() -> None:
    """DeepSeek: 5xx повторяется один раз."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return openai_response_with(vision_content())

    analyzer, client = make_deepseek(handler)
    try:
        verdict = asyncio.run(analyzer.analyze(make_ad(), "", IMAGE))
    finally:
        asyncio.run(client.aclose())

    assert calls["n"] == 2
    assert verdict is not None


def test_deepseek_broken_json_returns_none() -> None:
    """DeepSeek: битый JSON → None."""

    def handler(request: httpx.Request) -> httpx.Response:
        return openai_response_with("not json")

    analyzer, client = make_deepseek(handler)
    try:
        assert asyncio.run(analyzer.analyze(make_ad(), "", IMAGE)) is None
    finally:
        asyncio.run(client.aclose())


class StubVision:
    """Подделка vision-клиента для fallback-тестов."""

    def __init__(
        self,
        verdict: VisionVerdict | None,
        model: str,
        error: Exception | None = None,
    ) -> None:
        self.verdict = verdict
        self.model = model
        self.error = error
        self.calls = 0

    async def analyze(
        self, ad: Ad, body: str, images: list[tuple[str, str]]
    ) -> VisionVerdict | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.verdict


def make_verdict(confidence: float, model: str = "stub") -> VisionVerdict:
    """VisionVerdict с заданной уверенностью."""
    return VisionVerdict(
        is_silver=True,
        item_type="украшение",
        weight_g=10.0,
        purity=925,
        hallmark="зубр",
        scale_visible=True,
        stones_or_costume=False,
        confidence=confidence,
        notes="",
        question="",
        model=model,
    )


def test_fallback_on_none_primary() -> None:
    """Primary None → вердикт fallback."""
    primary = StubVision(None, "primary")
    fallback = StubVision(make_verdict(0.8, "fallback"), "fallback")
    analyzer = FallbackVisionAnalyzer(primary, fallback, min_confidence=0.5)

    verdict = asyncio.run(analyzer.analyze(make_ad(), "", IMAGE))

    assert verdict is not None
    assert verdict.model == "fallback"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_fallback_on_low_confidence() -> None:
    """Primary confidence 0.2 → fallback."""
    primary = StubVision(make_verdict(0.2, "primary"), "primary")
    fallback = StubVision(make_verdict(0.8, "fallback"), "fallback")
    analyzer = FallbackVisionAnalyzer(primary, fallback, min_confidence=0.5)

    verdict = asyncio.run(analyzer.analyze(make_ad(), "", IMAGE))

    assert verdict is not None
    assert verdict.model == "fallback"
    assert fallback.calls == 1


def test_fallback_not_called_on_high_confidence() -> None:
    """Primary confidence 0.9 → fallback не вызывается."""
    primary = StubVision(make_verdict(0.9, "primary"), "primary")
    fallback = StubVision(make_verdict(0.9, "fallback"), "fallback")
    analyzer = FallbackVisionAnalyzer(primary, fallback, min_confidence=0.5)

    verdict = asyncio.run(analyzer.analyze(make_ad(), "", IMAGE))

    assert verdict is not None
    assert verdict.model == "primary"
    assert fallback.calls == 0


def test_fallback_none_returns_primary() -> None:
    """Fallback None → вернуть primary, не падать."""
    primary = StubVision(make_verdict(0.2, "primary"), "primary")
    fallback = StubVision(None, "fallback")
    analyzer = FallbackVisionAnalyzer(primary, fallback, min_confidence=0.5)

    verdict = asyncio.run(analyzer.analyze(make_ad(), "", IMAGE))

    assert verdict is not None
    assert verdict.model == "primary"
    assert fallback.calls == 1


def test_fallback_error_returns_primary() -> None:
    """Ошибка fallback не роняет анализ."""
    primary = StubVision(None, "primary")
    fallback = StubVision(
        None, "fallback", error=RuntimeError("boom")
    )
    analyzer = FallbackVisionAnalyzer(primary, fallback, min_confidence=0.5)

    assert asyncio.run(analyzer.analyze(make_ad(), "", IMAGE)) is None


def make_config_ns(
    provider: str = "deepseek",
    fallback_provider: str = "none",
    key: str | None = "key",
) -> SimpleNamespace:
    """Конфиг-заглушка для фабрики vision."""
    return SimpleNamespace(
        vision_provider=provider,
        vision_api_key=key,
        vision_base_url="https://vision.example/v1",
        vision_model="deepseek-v4.1-flash",
        vision_timeout=60,
        vision_max_images=3,
        vision_fallback_provider=fallback_provider,
        vision_fallback_api_key=key,
        vision_fallback_base_url="https://fallback.example/v1",
        vision_fallback_model="claude-haiku-5-5",
        vision_min_confidence_fallback=0.5,
        llm_session_id="ses_silverwatch_ai",
    )


def test_factory_deepseek_and_claude_fallback() -> None:
    """Фабрика: DeepSeek primary, Claude fallback, none и без ключа."""
    from app.analyzers.factory import build_vision_analyzer

    deepseek = build_vision_analyzer(make_config_ns())
    assert isinstance(deepseek, DeepSeekVisionAnalyzer)

    claude = build_vision_analyzer(
        make_config_ns(provider="anthropic")
    )
    assert isinstance(claude, VisionAnalyzer)

    combined = build_vision_analyzer(
        make_config_ns(fallback_provider="anthropic")
    )
    assert isinstance(combined, FallbackVisionAnalyzer)
    assert isinstance(combined.primary, DeepSeekVisionAnalyzer)
    assert isinstance(combined.fallback, VisionAnalyzer)

    assert isinstance(
        build_vision_analyzer(make_config_ns(provider="none")),
        NullVisionAnalyzer,
    )
    assert isinstance(
        build_vision_analyzer(make_config_ns(key=None)),
        NullVisionAnalyzer,
    )
