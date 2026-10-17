"""Тесты эвристического парсера серебра."""

from __future__ import annotations

import pytest

from app.models import Ad
from app.scrap import ScrapPrices, default_scrap_prices
from app.silver import analyze_ad


def make_ad(price_byn: int | None = 2499) -> Ad:
    """Объявление для парсера."""
    return Ad(
        ad_id="1",
        subject="серебро",
        price_byn=price_byn,
        url="https://www.kufar.by/item/1",
        list_time=None,
        raw={},
    )


def analyze(body: str, price_byn: int | None = 2499, prices: ScrapPrices | None = None):
    """Короткий вызов парсера."""
    return analyze_ad(make_ad(price_byn), body, prices or default_scrap_prices())


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("вес 15 г", 15.0),
        ("общий вес 15 г", 15.0),
        ("15-грамм", 15.0),
        ("8, 26гр", 8.26),
        ("1,75гр", 1.75),
        ("62,77гр", 62.77),
        ("12 грамм", 12.0),
        ("около 35 грамм", 35.0),
    ],
)
def test_weight_patterns(body: str, expected: float) -> None:
    """Основные варианты записи веса."""
    assert analyze(body, price_byn=None).weight_g == expected


def test_years_and_units_are_not_weight() -> None:
    """Годы, сантиметры и миллиметры не считаются весом."""
    assert analyze("Монета 1881 г., 2009г, вес 12 г", None).weight_g == 12.0
    assert analyze("размер 15 см, 12 мм, вес 10 г", None).weight_g == 10.0


def test_purity_is_not_weight() -> None:
    """925 без слова вес — проба, а не граммы."""
    result = analyze("Серебро 925, вес 10 г", None)

    assert result.weight_g == 10.0
    assert result.purity == 925


def test_weight_range_gives_none() -> None:
    """Диапазон веса не угадывается."""
    result = analyze("от 7.5 до 9.5 грамм, 925 проба", None)

    assert result.weight_g is None
    assert "диапазон веса" in result.note


def test_multiple_weights_without_total_gives_none() -> None:
    """Несколько разных весов без «общий вес» — None."""
    result = analyze("вес 10 г и 20 г, 925 проба", None)

    assert result.weight_g is None
    assert "несколько весов" in result.note


def test_general_weight_wins() -> None:
    """При «общий вес» берётся он, а не вес одной монеты."""
    result = analyze("общий вес 15 г, одна монета 1 г, 925 проба", None)

    assert result.weight_g == 15.0


@pytest.mark.parametrize("purity", [999, 958, 925, 916, 900, 875, 830, 800, 750, 500])
def test_purity_values(purity: int) -> None:
    """Поддерживаемые пробы."""
    assert analyze(f"{purity} проба, вес 10 г", None).purity == purity


def test_purity_ag_and_soviet() -> None:
    """Ag 925, 84→875, 88→916."""
    assert analyze("Ag 925, вес 10 г", None).purity == 925
    assert analyze("84 проба, вес 10 г", None).purity == 875
    assert analyze("88 проба, вес 10 г", None).purity == 916


def test_multiple_purities_take_minimum() -> None:
    """Из нескольких проб берётся минимальная."""
    result = analyze("925 и 875, вес 10 г, клеймо", None)

    assert result.purity == 875
    assert "несколько проб" in result.note


def test_hallmark_fallback() -> None:
    """925 без клейма считается как 800; с клеймом — 925."""
    no_mark = analyze("925 проба, вес 10 г", None)
    with_mark = analyze("925 проба, вес 10 г, клеймо", None)

    assert no_mark.effective_purity == 800
    assert "925 без клейма" in no_mark.note
    assert with_mark.effective_purity == 925


def test_below_and_above_scrap() -> None:
    """Ниже лома: цена 10 BYN за 10 г при ломе 56 BYN."""
    below = analyze("925 проба, вес 10 г, клеймо", price_byn=1000)
    above = analyze("925 проба, вес 10 г, клеймо", price_byn=10000)

    assert below.price_byn == 10.0
    assert below.scrap_price == pytest.approx(56.0)
    assert below.is_below_scrap is True
    assert above.is_below_scrap is False


@pytest.mark.parametrize(
    "phrase", ["цена за 1 грамм", "за грамм", "р/г", "руб/гр"]
)
def test_per_gram_pricing(phrase: str) -> None:
    """Цена за грамм не сравнивается с ломом как цена за лот."""
    result = analyze(f"925 проба, вес 10 г, клеймо, {phrase}", price_byn=400)

    assert result.per_gram_pricing is True
    assert result.price_per_gram == 4.0
    assert result.scrap_price == pytest.approx(56.0)
    assert result.is_below_scrap is True


def test_missing_data_is_not_below() -> None:
    """Без веса/пробы/цены не считаем ниже лома."""
    for body, price in [
        ("", 2499),
        ("925 проба", 2499),
        ("вес 10 г", 2499),
        ("925 проба, вес 10 г", None),
    ]:
        result = analyze(body, price)
        assert result.is_below_scrap is False
        assert result.note


def test_no_scrap_rate_for_unknown_purity() -> None:
    """Для пробы без ставки scrap_price не считается."""
    result = analyze("958 проба, вес 10 г", 1000)

    assert result.effective_purity == 958
    assert result.scrap_price is None
    assert result.is_below_scrap is False
    assert "нет цены лома" in result.note
