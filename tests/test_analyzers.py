"""Тесты AI-фильтра на фейковом HTTP, без сети и ключа."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.analyzers.deepseek import DeepSeekAnalyzer
from app.analyzers.factory import build_analyzer
from app.analyzers.null import NullAnalyzer
from app.models import Ad
from app.scrap import default_scrap_prices
from app.silver import analyze_ad


def context():
    """Объявление, body и regex-разбор."""
    ad = Ad(
        ad_id="1",
        subject="серебро",
        price_byn=10000,
        url="https://www.kufar.by/item/1",
        list_time=None,
        raw={"category": "украшения", "region": "Минск"},
    )
    body = "925 проба, вес 10 г"
    prices = default_scrap_prices()
    return ad, body, analyze_ad(ad, body, prices), prices


def content(**overrides: object) -> str:
    """Строгий JSON AI-ответа."""
    data: dict[str, object] = {
        "is_silver": True,
        "category": "украшение",
        "verdict": "look",
        "reason": "Похоже на серебро, стоит проверить клеймо.",
        "question": "Есть ли клеймо?",
        "weight_g": 10,
        "purity": 925,
        "confidence": 0.8,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def make_analyzer(handler, key: str = "secret-key") -> tuple[DeepSeekAnalyzer, httpx.AsyncClient]:
    """Собрать анализатор с MockTransport."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    analyzer = DeepSeekAnalyzer(
        base_url="https://llm.example/v1",
        api_key=key,
        model="deepseek-chat",
        timeout=5,
        client=client,
    )
    return analyzer, client


def test_analyze_parses_verdict_and_payload() -> None:
    """Корректный JSON разбирается, запрос идёт на chat/completions."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content()}}]},
        )

    ad, body, analysis, prices = context()
    analyzer, client = make_analyzer(handler)
    try:
        verdict = asyncio.run(analyzer.analyze(ad, body, analysis, prices))
    finally:
        asyncio.run(client.aclose())

    assert verdict is not None
    assert verdict.is_silver is True
    assert verdict.verdict == "look"
    assert verdict.weight_g == 10.0
    assert verdict.purity == 925
    assert verdict.reason.startswith("Похоже")
    assert verdict.model == "deepseek-chat"
    assert verdict.unprofitable_prob == 0.0
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-key"
    payload = seen["payload"]
    assert payload["response_format"] == {"type": "json_object"}
    assert "925 проба" in payload["messages"][1]["content"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.55, 0.55), (2.0, 1.0), (-1.0, 0.0), ("0.3", 0.3), ("abc", 0.0)],
)
def test_unprofitable_prob_parsed_and_clamped(
    value: object, expected: float
) -> None:
    """unprofitable_prob читается и зажимается в 0..1."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": content(unprofitable_prob=value)}}
                ]
            },
        )

    ad, body, analysis, prices = context()
    analyzer, client = make_analyzer(handler)
    try:
        verdict = asyncio.run(analyzer.analyze(ad, body, analysis, prices))
    finally:
        asyncio.run(client.aclose())

    assert verdict is not None
    assert verdict.unprofitable_prob == pytest.approx(expected)
    system_prompt = seen["payload"]["messages"][0]["content"]
    assert "unprofitable_prob" in system_prompt


def test_json_is_extracted_from_text() -> None:
    """JSON можно вырезать, если модель добавила текст вокруг."""
    wrapped = f"Вот результат:\n{content()}\nГотово."

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": wrapped}}]}
        )

    ad, body, analysis, prices = context()
    analyzer, client = make_analyzer(handler)
    try:
        verdict = asyncio.run(analyzer.analyze(ad, body, analysis, prices))
    finally:
        asyncio.run(client.aclose())

    assert verdict is not None
    assert verdict.verdict == "look"


def test_broken_json_returns_none() -> None:
    """Битый JSON → None, без исключения."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "not json"}}]}
        )

    ad, body, analysis, prices = context()
    analyzer, client = make_analyzer(handler)
    try:
        assert asyncio.run(analyzer.analyze(ad, body, analysis, prices)) is None
    finally:
        asyncio.run(client.aclose())


def test_429_retries_once() -> None:
    """На 429 делается один повтор."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content()}}]}
        )

    ad, body, analysis, prices = context()
    analyzer, client = make_analyzer(handler)
    try:
        verdict = asyncio.run(analyzer.analyze(ad, body, analysis, prices))
    finally:
        asyncio.run(client.aclose())

    assert calls["n"] == 2
    assert verdict is not None


def test_timeout_returns_none() -> None:
    """Таймаут → None и fallback."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    ad, body, analysis, prices = context()
    analyzer, client = make_analyzer(handler)
    try:
        assert asyncio.run(analyzer.analyze(ad, body, analysis, prices)) is None
    finally:
        asyncio.run(client.aclose())


def test_missing_key_returns_none_without_request() -> None:
    """Без ключа запрос не выполняется."""
    ad, body, analysis, prices = context()
    analyzer = DeepSeekAnalyzer(api_key="")
    assert asyncio.run(analyzer.analyze(ad, body, analysis, prices)) is None


def test_factory_none_and_missing_key() -> None:
    """none → NullAnalyzer; deepseek без ключа → NullAnalyzer."""
    none_config = SimpleNamespace(
        llm_provider="none",
        llm_api_key=None,
        llm_base_url=None,
        llm_model=None,
        llm_timeout=20,
    )
    no_key = SimpleNamespace(
        llm_provider="deepseek",
        llm_api_key=None,
        llm_base_url="https://api.deepseek.com/v1",
        llm_model="deepseek-chat",
        llm_timeout=20,
    )
    with_key = SimpleNamespace(
        llm_provider="deepseek",
        llm_api_key="secret",
        llm_base_url="https://api.deepseek.com/v1",
        llm_model="deepseek-chat",
        llm_timeout=20,
    )

    assert isinstance(build_analyzer(none_config), NullAnalyzer)  # type: ignore[arg-type]
    assert isinstance(build_analyzer(no_key), NullAnalyzer)  # type: ignore[arg-type]
    assert isinstance(build_analyzer(with_key), DeepSeekAnalyzer)  # type: ignore[arg-type]
