"""Tkinter-интерфейс SilverCoockold: статус, управление и хвост лога."""

from __future__ import annotations

import os
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

from app.config import BASE_DIR
from app.control import (
    DEFAULT_CONTROL_PATH,
    ControlState,
    read_control,
    write_control,
)
from app.status import DEFAULT_STATUS_PATH, is_alive, read_status

CONTROL_PATH = Path(os.getenv("CONTROL_PATH") or DEFAULT_CONTROL_PATH)
STATUS_PATH = Path(os.getenv("STATUS_PATH") or DEFAULT_STATUS_PATH)
LOG_PATH = Path(os.getenv("LOG_PATH") or (BASE_DIR / "logs" / "app.log"))
LOG_TAIL_LINES = 15


def read_log_tail(path: str | Path, lines: int = LOG_TAIL_LINES) -> str:
    """Прочитать последние строки лога, не загружая файл целиком."""
    try:
        with Path(path).open("rb") as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(max(0, size - 8192))
            text = file.read().decode("utf-8", errors="replace")
    except OSError:
        return "Лог пока пуст."
    tail = text.splitlines()[-lines:]
    return "\n".join(tail) if tail else "Лог пока пуст."


class SilverCoockoldUI:
    """Окно статуса и управления демоном."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SilverCoockold")
        self.root.geometry("560x460")
        self.root.minsize(520, 420)

        if not CONTROL_PATH.exists():
            write_control(CONTROL_PATH, ControlState())

        state = read_control(CONTROL_PATH)
        self.tracking_var = tk.BooleanVar(value=state.tracking)
        self.prevent_sleep_var = tk.BooleanVar(value=state.prevent_sleep)
        self.daemon_var = tk.StringVar(value="Демон: проверка…")
        self.tracking_status_var = tk.StringVar()
        self.sleep_status_var = tk.StringVar()
        self.ads_var = tk.StringVar(value="Объявлений в базе: —")
        self.last_poll_var = tk.StringVar(value="Последний опрос: —")

        self._build()
        self.refresh()

    def _build(self) -> None:
        """Собрать виджеты окна."""
        title_font = tkfont.nametofont("TkDefaultFont").copy()
        title_font.configure(size=16, weight="bold")

        tk.Label(self.root, text="SilverCoockold", font=title_font).pack(
            anchor="w", padx=16, pady=(14, 6)
        )

        status = ttk.LabelFrame(self.root, text="Состояние")
        status.pack(fill="x", padx=12, pady=6)
        ttk.Label(status, textvariable=self.daemon_var).pack(
            anchor="w", padx=10, pady=(6, 2)
        )
        ttk.Label(status, textvariable=self.tracking_status_var).pack(
            anchor="w", padx=10, pady=2
        )
        ttk.Label(status, textvariable=self.sleep_status_var).pack(
            anchor="w", padx=10, pady=2
        )
        ttk.Label(status, textvariable=self.ads_var).pack(
            anchor="w", padx=10, pady=2
        )
        ttk.Label(status, textvariable=self.last_poll_var).pack(
            anchor="w", padx=10, pady=(2, 8)
        )

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", padx=12, pady=6)
        tk.Button(
            buttons,
            text="Включить отслеживание",
            bg="#2e7d32",
            fg="white",
            activebackground="#1b5e20",
            activeforeground="white",
            command=lambda: self.set_tracking(True),
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            buttons,
            text="Отключить",
            bg="#c62828",
            fg="white",
            activebackground="#8e0000",
            activeforeground="white",
            command=lambda: self.set_tracking(False),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Обновить", command=self.refresh).pack(side="left")

        ttk.Checkbutton(
            self.root,
            text="Не давать ПК спать",
            variable=self.prevent_sleep_var,
            command=self.on_sleep_change,
        ).pack(anchor="w", padx=16, pady=4)

        log_frame = ttk.LabelFrame(self.root, text="logs/app.log — последние 15 строк")
        log_frame.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(
            log_frame,
            height=12,
            wrap="none",
            state="disabled",
            font=tkfont.nametofont("TkFixedFont"),
            yscrollcommand=scrollbar.set,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.log_text.yview)

    def set_tracking(self, enabled: bool) -> None:
        """Записать новое значение tracking в control.json."""
        state = read_control(CONTROL_PATH)
        write_control(
            CONTROL_PATH,
            ControlState(tracking=enabled, prevent_sleep=state.prevent_sleep),
        )
        self.refresh()

    def on_sleep_change(self) -> None:
        """Записать новое значение prevent_sleep в control.json."""
        state = read_control(CONTROL_PATH)
        write_control(
            CONTROL_PATH,
            ControlState(
                tracking=state.tracking,
                prevent_sleep=self.prevent_sleep_var.get(),
            ),
        )
        self.refresh()

    def refresh(self) -> None:
        """Обновить статус и хвост лога; повторять каждые 2 секунды."""
        state = read_control(CONTROL_PATH)
        self.tracking_var.set(state.tracking)
        self.prevent_sleep_var.set(state.prevent_sleep)
        self.tracking_status_var.set(
            f"Отслеживание: {'включено' if state.tracking else 'выключено'}"
        )
        self.sleep_status_var.set(
            f"Режим сна: {'запрещён' if state.prevent_sleep else 'разрешён'}"
        )

        status = read_status(STATUS_PATH)
        if status is None:
            self.daemon_var.set("Демон: не запущен")
            self.ads_var.set("Объявлений в базе: —")
            self.last_poll_var.set("Последний опрос: —")
        else:
            if is_alive(status):
                self.daemon_var.set("Демон: запущен")
            elif status.get("running") is False:
                self.daemon_var.set("Демон: остановлен")
            else:
                self.daemon_var.set("Демон: нет связи")
            self.ads_var.set(f"Объявлений в базе: {status.get('ads_total', '—')}")
            polls = status.get("last_poll")
            if isinstance(polls, dict) and polls:
                latest = max(str(value) for value in polls.values())
            else:
                latest = "—"
            self.last_poll_var.set(f"Последний опрос: {latest}")

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", read_log_tail(LOG_PATH))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

        self.root.after(2000, self.refresh)


def main() -> None:
    """Запустить окно."""
    root = tk.Tk()
    SilverCoockoldUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
