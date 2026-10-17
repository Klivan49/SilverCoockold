"""Управление демоном через data/control.json (без портов и сети)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

DEFAULT_CONTROL_PATH = BASE_DIR / "data" / "control.json"


@dataclass
class ControlState:
    """Состояние, которым UI управляет демоном."""

    tracking: bool = True
    prevent_sleep: bool = False


def _utc_now_iso() -> str:
    """Текущее время UTC в ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def read_control(path: str | Path) -> ControlState:
    """Прочитать control.json; при ошибке вернуть значения по умолчанию."""
    path = Path(path)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("control: файл %s не найден, используются значения по умолчанию", path)
        return ControlState()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "control: не удалось прочитать %s (%s), используются значения по умолчанию",
            path,
            exc,
        )
        return ControlState()

    if not isinstance(payload, dict):
        logger.warning("control: %s должен содержать JSON-объект, взяты значения по умолчанию", path)
        return ControlState()

    tracking = payload.get("tracking", True)
    prevent_sleep = payload.get("prevent_sleep", False)
    if not isinstance(tracking, bool) or not isinstance(prevent_sleep, bool):
        logger.warning("control: некорректные типы полей в %s, взяты значения по умолчанию", path)
        return ControlState()

    return ControlState(tracking=tracking, prevent_sleep=prevent_sleep)


def write_control(path: str | Path, state: ControlState) -> None:
    """Атомарно записать control.json с updated_at в UTC."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tracking": state.tracking,
        "prevent_sleep": state.prevent_sleep,
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
