"""Smoke-тесты UI: редактор цен лома без сети."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.scrap import load_scrap_prices


def make_root():
    """Создать скрытый Tk root или пропустить тест без дисплея."""
    if not os.environ.get("DISPLAY"):
        pytest.skip("нет DISPLAY для Tkinter")
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tkinter недоступен: {exc}")
    root.withdraw()
    return root


def test_scrap_editor_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Окно цен открывается, сохраняет валидные значения и не ломает файл."""
    import ui

    control = tmp_path / "control.json"
    status = tmp_path / "status.json"
    scrap = tmp_path / "scrap_prices.json"
    log = tmp_path / "app.log"
    monkeypatch.setattr(ui, "CONTROL_PATH", control)
    monkeypatch.setattr(ui, "STATUS_PATH", status)
    monkeypatch.setattr(ui, "SCRAP_PATH", scrap)
    monkeypatch.setattr(ui, "LOG_PATH", log)

    root = make_root()
    try:
        app = ui.SilverCoockoldUI(root)
        root.update()
        editor = app.open_scrap_editor()
        root.update()

        assert editor.window.title() == "Цены лома, BYN/г"
        assert len(editor.entries) == 10
        assert set(editor.entries) == set(ui.PURITY_LIST)

        editor.entries[925].set("6,2")
        editor.no_hallmark_var.set("875")
        assert editor.save() is True
        assert editor.status_var.get().startswith("Сохранено ")

        saved = load_scrap_prices(scrap)
        assert saved.prices[925] == pytest.approx(6.2)
        assert saved.no_hallmark_purity == 875

        before = scrap.read_text(encoding="utf-8")
        errors: list[tuple] = []
        monkeypatch.setattr(
            ui.messagebox,
            "showerror",
            lambda *args, **kwargs: errors.append(args),
        )
        editor.entries[999].set("abc")
        assert editor.save() is False
        assert errors
        assert scrap.read_text(encoding="utf-8") == before

        editor.reset()
        assert editor.entries[925].get() == "5,6"
        assert editor.entries[958].get() == ""
        assert editor.no_hallmark_var.get() == "800"
        assert scrap.read_text(encoding="utf-8") == before

        editor.close()
    finally:
        root.destroy()
