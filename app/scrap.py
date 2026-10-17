"""Цены лома серебра и правило клейма."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

DEFAULT_PRICES: dict[int, float] = {
    999: 5.9,
    925: 5.6,
    916: 5.5,
    900: 5.4,
    875: 5.0,
    830: 5.0,
    800: 5.0,
    750: 4.7,
    500: 3.2,
}
DEFAULT_NO_HALLMARK_PURITY = 800
DEFAULT_SCRAP_PATH = BASE_DIR / "data" / "scrap_prices.json"
HALLMARK_RE = re.compile(r"зубр|кокошник|клеймо|пломба|госпроба", re.IGNORECASE)


@dataclass
class ScrapPrices:
    """Цены лома BYN/грамм и запасная проба без клейма."""

    prices: dict[int, float]
    no_hallmark_purity: int = DEFAULT_NO_HALLMARK_PURITY
    updated_at: str | None = None


def _utc_now_iso() -> str:
    """Текущее время UTC в ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def default_scrap_prices() -> ScrapPrices:
    """Вернуть цены по умолчанию."""
    return ScrapPrices(prices=dict(DEFAULT_PRICES), updated_at=None)


def load_scrap_prices(path: str | Path = DEFAULT_SCRAP_PATH) -> ScrapPrices:
    """Прочитать data/scrap_prices.json; при ошибке вернуть defaults."""
    path = Path(path)
    if not path.is_file():
        return default_scrap_prices()
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "scrap: не удалось прочитать %s (%s), взяты цены по умолчанию",
            path,
            exc,
        )
        return default_scrap_prices()
    if not isinstance(payload, dict):
        logger.warning("scrap: %s должен содержать JSON-объект", path)
        return default_scrap_prices()

    prices = dict(DEFAULT_PRICES)
    raw_prices = payload.get("prices")
    if isinstance(raw_prices, dict):
        for key, value in raw_prices.items():
            try:
                purity = int(key)
            except (TypeError, ValueError):
                logger.warning("scrap: некорректная проба %r, пропуск", key)
                continue
            if purity <= 0:
                logger.warning("scrap: проба должна быть > 0, получено %r", key)
                continue
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value <= 0
            ):
                logger.warning(
                    "scrap: цена для пробы %s должна быть > 0, оставлено значение по умолчанию",
                    purity,
                )
                continue
            prices[purity] = float(value)

    no_hallmark = payload.get("no_hallmark_purity", DEFAULT_NO_HALLMARK_PURITY)
    if (
        not isinstance(no_hallmark, int)
        or isinstance(no_hallmark, bool)
        or no_hallmark <= 0
    ):
        logger.warning("scrap: no_hallmark_purity должна быть целым > 0, взято 800")
        no_hallmark = DEFAULT_NO_HALLMARK_PURITY

    updated_at = payload.get("updated_at")
    return ScrapPrices(
        prices=prices,
        no_hallmark_purity=no_hallmark,
        updated_at=updated_at if isinstance(updated_at, str) else None,
    )


def save_scrap_prices(
    path: str | Path, data: ScrapPrices
) -> None:
    """Атомарно сохранить цены лома."""
    if data.no_hallmark_purity <= 0:
        raise ValueError("no_hallmark_purity должна быть > 0")
    for purity, price in data.prices.items():
        if not isinstance(purity, int) or purity <= 0:
            raise ValueError(f"некорректная проба: {purity!r}")
        if (
            not isinstance(price, (int, float))
            or isinstance(price, bool)
            or price <= 0
        ):
            raise ValueError(f"цена для пробы {purity} должна быть > 0")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "prices": {
            str(purity): float(price)
            for purity, price in sorted(data.prices.items())
        },
        "no_hallmark_purity": data.no_hallmark_purity,
        "updated_at": _utc_now_iso(),
    }
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def hallmark_found(text: str) -> bool:
    """Найти упоминание клейма/пробы на изделии."""
    return HALLMARK_RE.search(text) is not None


def effective_purity(
    purity: int | None, has_hallmark: bool, prices: ScrapPrices
) -> int | None:
    """Учесть правило: 925 без клейма считается как no_hallmark_purity."""
    if purity is None:
        return None
    if purity == 925 and not has_hallmark:
        return prices.no_hallmark_purity
    return purity


def scrap_rate(prices: ScrapPrices, purity: int | None) -> float | None:
    """Цена лома BYN/грамм для пробы."""
    if purity is None:
        return None
    return prices.prices.get(purity)
