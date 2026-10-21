"""Эвристический парсер веса, пробы и цены серебряного лота."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import Ad
from app.scrap import (
    ScrapPrices,
    effective_purity,
    hallmark_found,
    scrap_rate,
)

PURITY_VALUES = {999, 958, 925, 916, 900, 875, 830, 800, 750, 500}
SOVIET_PURITY = {84: 875, 88: 916}

_NUM = r"\d+(?:\s*[,.]\s*\d+)?"
_WEIGHT_RE = re.compile(
    rf"(?P<num>{_NUM})\s*-?\s*(?P<unit>грамм(?:а|ов)?|гр|г|g)\b",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(
    rf"(?:от\s+)?(?P<a>{_NUM})\s*(?:до|[-–—])\s*(?P<b>{_NUM})\s*"
    r"(?:грамм(?:а|ов)?|гр|г|g)\b",
    re.IGNORECASE,
)
_GENERAL_WEIGHT_RE = re.compile(r"общ(?:ий|ая|его)\s+(?:вес|масс)", re.IGNORECASE)
_WEIGHT_CONTEXT_RE = re.compile(r"вес|масса|весит|общ", re.IGNORECASE)
_PER_GRAM_RE = re.compile(
    r"(?:за\s*(?:1\s*)?(?:грамм(?:а|ов)?|гр|г)\b)"
    r"|(?:р\s*/\s*г\b)"
    r"|(?:руб\s*/\s*гр\b)",
    re.IGNORECASE,
)
_BODY_PRICE_RE = re.compile(
    r"(?:цена|стоимость|продам|отдам|за\s+всё|за\s+все)\s*[:\-—]?\s*"
    r"(\d{1,6}(?:\s*[,.]\s*\d{1,2})?)\s*"
    r"(?:руб(?:лей|ля|\.)?|BYN|р\b)",
    re.IGNORECASE,
)
_SILVER_RE = re.compile(
    r"серебр|925|875|916|900|830|800|999|стерлинг|sterling|\bAg\b",
    re.IGNORECASE,
)
_NOT_SILVER_RE = re.compile(
    r"посеребр|мельхиор|латун|бижутер|нержавеющ|сталь|"
    r"покрытие\s+серебр|alpacca|нейзильбер|silver.?plated|"
    r"штаны|брюк|куртк|пуховик|плать|джинс|юбк|свитер|футболк|"
    r"обув|кроссовк|сапог|ботинк|сумк|рюкзак|зонт|"
    r"серебр\w*\s+цвет|серебрист|металлик",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![\d.,])(\d{2,3})(?!\d)")


@dataclass
class SilverAnalysis:
    """Результат разбора объявления."""

    weight_g: float | None
    purity: int | None
    effective_purity: int | None
    price_byn: float | None
    price_per_gram: float | None
    scrap_price: float | None
    is_below_scrap: bool
    per_gram_pricing: bool
    note: str
    is_suspicious: bool = False
    is_silver: bool = True
    is_lead: bool = False


def _to_float(raw: str) -> float | None:
    """Преобразовать «8, 26» или «8.26» в float."""
    cleaned = raw.replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _is_silver(text: str) -> bool:
    """Похоже ли объявление на изделие из серебра, а не на посеребрение."""
    if _NOT_SILVER_RE.search(text) and not re.search(
        r"\b(?:925|875|916|900|830|800|999)\b", text
    ):
        return False
    return _SILVER_RE.search(text) is not None


def _weight_context(text: str, position: int) -> bool:
    """Есть ли рядом слово вес/масса."""
    chunk = text[max(0, position - 30) : position]
    return _WEIGHT_CONTEXT_RE.search(chunk) is not None


def _extract_weight(text: str, notes: list[str]) -> float | None:
    """Вытащить вес в граммах; при неоднозначности вернуть None."""
    if _RANGE_RE.search(text):
        notes.append("диапазон веса")
        return None

    matches: list[tuple[int, float]] = []
    for match in _WEIGHT_RE.finditer(text):
        raw = match.group("num")
        value = _to_float(raw)
        if value is None or value <= 0:
            continue
        if value > 1000:
            notes.append("вес нереалистичен")
            continue
        unit = match.group("unit").lower()
        has_decimal = "," in raw or "." in raw
        if re.search(r"за\s*$", text[max(0, match.start() - 8) : match.start()]):
            continue
        if (
            not has_decimal
            and 1800 <= value <= 2100
            and unit in {"г", "гр", "g"}
        ):
            continue
        if (
            not has_decimal
            and value == 925
            and not _weight_context(text, match.start())
        ):
            continue
        matches.append((match.start(), value))

    if not matches:
        return None

    general = _GENERAL_WEIGHT_RE.search(text)
    if general is not None:
        for position, value in matches:
            if position >= general.start():
                return value
        return matches[0][1]

    unique = {round(value, 3) for _, value in matches}
    if len(unique) > 1:
        notes.append("несколько весов без общего веса")
        return None
    return matches[0][1]


def _extract_purity(text: str, notes: list[str]) -> int | None:
    """Вытащить пробу; при нескольких взять минимальную."""
    values: set[int] = set()
    for match in _NUMBER_RE.finditer(text):
        value = int(match.group(1))
        before = text[max(0, match.start() - 18) : match.start()].lower()
        after = text[match.end() : match.end() + 12].lower()

        if value in PURITY_VALUES:
            purity = value
        elif value in SOVIET_PURITY and any(
            word in before + after
            for word in ("серебр", "проб", "пр.", "ag", "клейм", "совет")
        ):
            purity = SOVIET_PURITY[value]
        else:
            continue

        if re.match(r"\s*(?:грамм(?:а|ов)?|гр|г|g)\b", after):
            continue
        if any(word in before for word in ("цена", "руб", "byn", "стоимость", "₽")):
            continue
        if any(word in after for word in ("руб", "byn", "стоимость", "₽", "цена")):
            continue
        values.add(purity)

    if not values:
        return None
    if len(values) > 1:
        notes.append("несколько проб, взята минимальная")
    return min(values)


def _per_gram_pricing(text: str) -> bool:
    """Понять, указана ли цена за грамм."""
    return _PER_GRAM_RE.search(text) is not None


def _extract_body_price(text: str) -> float | None:
    """Найти явную общую цену в описании: «Цена 1500 рублей»."""
    match = _BODY_PRICE_RE.search(text)
    if match is None:
        return None
    value = _to_float(match.group(1))
    return value if value is not None and value > 0 else None


def analyze_ad(
    ad: Ad,
    body: str,
    prices: ScrapPrices,
    lead_max_price_byn: float = 100.0,
    lead_negotiable: bool = True,
) -> SilverAnalysis:
    """Разобрать объявление и посчитать, дешевле ли оно лома."""
    text = f"{ad.subject}\n{body or ''}".strip()
    notes: list[str] = []
    if not _is_silver(text):
        return SilverAnalysis(
            weight_g=None,
            purity=None,
            effective_purity=None,
            price_byn=None,
            price_per_gram=None,
            scrap_price=None,
            is_below_scrap=False,
            per_gram_pricing=False,
            note="не серебро",
            is_suspicious=False,
            is_silver=False,
            is_lead=False,
        )
    if not (body or "").strip():
        notes.append("описание пустое")

    weight = _extract_weight(text, notes)
    purity = _extract_purity(text, notes)
    has_hallmark = hallmark_found(text)
    effective = effective_purity(purity, has_hallmark, prices)
    if purity == 925 and not has_hallmark:
        notes.append(f"925 без клейма, эффективная проба {effective}")

    listing_price = (
        ad.price_byn / 100.0 if ad.price_byn and ad.price_byn > 0 else None
    )
    per_gram = _per_gram_pricing(text)
    body_price = _extract_body_price(text)
    price_per_gram: float | None = None
    total_price: float | None = None

    if per_gram:
        if listing_price is not None:
            price_per_gram = listing_price
            if weight is not None:
                total_price = price_per_gram * weight
            notes.append("цена за грамм")
    else:
        total_price = listing_price
        if body_price is not None and (
            total_price is None or body_price > total_price * 1.5
        ):
            total_price = body_price
            notes.append(f"цена из описания: {body_price:g} BYN")
        if total_price is not None and weight is not None and weight > 0:
            price_per_gram = total_price / weight

    if total_price is None and price_per_gram is None:
        notes.append("цена не указана")

    rate = scrap_rate(prices, effective)
    scrap_price: float | None = None
    if weight is not None and effective is not None:
        if rate is None:
            notes.append("нет цены лома для пробы")
        else:
            scrap_price = rate * weight
    if weight is None and not any(
        "вес" in note or "масса" in note for note in notes
    ):
        notes.append("вес не найден")
    if purity is None:
        notes.append("проба не найдена")

    suspicious = price_per_gram is not None and (
        price_per_gram < 1.0
        or (rate is not None and price_per_gram < rate * 0.15)
    )
    if suspicious:
        notes.append("подозрительно дешево: проверьте цену и вес")

    is_below = (
        total_price is not None
        and scrap_price is not None
        and total_price < scrap_price
        and not suspicious
    )
    is_lead = (
        (weight is None or purity is None)
        and (
            (
                total_price is not None
                and total_price <= lead_max_price_byn
            )
            or (
                total_price is None
                and lead_negotiable
                and purity is not None
            )
        )
        and not is_below
        and not suspicious
    )

    return SilverAnalysis(
        weight_g=weight,
        purity=purity,
        effective_purity=effective,
        price_byn=price_per_gram if per_gram else total_price,
        price_per_gram=price_per_gram,
        scrap_price=scrap_price,
        is_below_scrap=is_below,
        per_gram_pricing=per_gram,
        note="; ".join(notes),
        is_suspicious=suspicious,
        is_silver=True,
        is_lead=is_lead,
    )
