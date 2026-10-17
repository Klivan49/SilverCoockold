"""Тесты цен лома, файла настроек и правила клейма."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.scrap import (
    DEFAULT_PRICES,
    ScrapPrices,
    default_scrap_prices,
    effective_purity,
    hallmark_found,
    load_scrap_prices,
    save_scrap_prices,
)


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    """Нет файла → цены по умолчанию."""
    data = load_scrap_prices(tmp_path / "missing.json")

    assert data.prices == DEFAULT_PRICES
    assert data.no_hallmark_purity == 800


def test_broken_json_returns_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Битый JSON → defaults и предупреждение."""
    path = tmp_path / "scrap_prices.json"
    path.write_text("{broken", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.scrap"):
        data = load_scrap_prices(path)

    assert data.prices == DEFAULT_PRICES
    assert "не удалось прочитать" in caplog.text


def test_invalid_values_fall_back_to_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Нулевые/строковые цены не принимаются."""
    path = tmp_path / "scrap_prices.json"
    path.write_text(
        json.dumps(
            {
                "prices": {"925": 0, "875": "abc", "800": 4.4},
                "no_hallmark_purity": -1,
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="app.scrap"):
        data = load_scrap_prices(path)

    assert data.prices[925] == DEFAULT_PRICES[925]
    assert data.prices[875] == DEFAULT_PRICES[875]
    assert data.prices[800] == 4.4
    assert data.no_hallmark_purity == 800


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Сохранение и чтение цен, updated_at записывается."""
    path = tmp_path / "scrap_prices.json"
    prices = ScrapPrices(
        prices={925: 6.1, 875: 5.2},
        no_hallmark_purity=830,
    )

    save_scrap_prices(path, prices)
    loaded = load_scrap_prices(path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert loaded.prices[925] == 6.1
    assert loaded.prices[875] == 5.2
    assert loaded.prices[999] == DEFAULT_PRICES[999]
    assert loaded.no_hallmark_purity == 830
    assert loaded.updated_at == payload["updated_at"]


@pytest.mark.parametrize(
    "prices",
    [
        ScrapPrices(prices={925: 0}),
        ScrapPrices(prices={-1: 5.0}),
        ScrapPrices(prices={925: 5.0}, no_hallmark_purity=0),
    ],
)
def test_save_rejects_invalid_values(tmp_path: Path, prices: ScrapPrices) -> None:
    """save_scrap_prices валидирует пробы, цены и запасную пробу."""
    with pytest.raises(ValueError):
        save_scrap_prices(tmp_path / "scrap_prices.json", prices)


def test_hallmark_and_effective_purity(tmp_path: Path) -> None:
    """925 без клейма становится 800; с клеймом остаётся 925."""
    prices = default_scrap_prices()

    assert hallmark_found("Клеймо: кокошник") is True
    assert hallmark_found("зубр на ложке") is True
    assert hallmark_found("серебро 925") is False
    assert effective_purity(925, False, prices) == 800
    assert effective_purity(925, True, prices) == 925
    assert effective_purity(875, False, prices) == 875
    assert effective_purity(None, True, prices) is None
