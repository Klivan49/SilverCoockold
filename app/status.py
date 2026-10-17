"""data/status.json: состояние демона для UI."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

DEFAULT_STATUS_PATH = BASE_DIR / "data" / "status.json"
ALIVE_MAX_AGE_SECONDS = 30


def _utc_now_iso() -> str:
    """Текущее время UTC в ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def write_status(
    path: str | Path,
    *,
    running: bool,
    tracking: bool,
    prevent_sleep: bool,
    ads_total: int,
    last_poll: dict[str, str],
) -> None:
    """Атомарно записать status.json с текущим updated_at."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "running": running,
        "tracking": tracking,
        "prevent_sleep": prevent_sleep,
        "ads_total": ads_total,
        "last_poll": last_poll,
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


def read_status(path: str | Path) -> dict[str, Any] | None:
    """Прочитать status.json; None, если файла нет или он битый."""
    path = Path(path)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("status: не удалось прочитать %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("status: %s должен содержать JSON-объект", path)
        return None
    return payload


def is_alive(
    status: dict[str, Any] | None, max_age_seconds: float = ALIVE_MAX_AGE_SECONDS
) -> bool:
    """Считать демон живым, если running=true и updated_at свежий."""
    if not status or status.get("running") is not True:
        return False
    updated_at = status.get("updated_at")
    if not isinstance(updated_at, str):
        return False
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    return age <= max_age_seconds
