"""Tkinter-интерфейс SilverCoockold: статус, управление и хвост лога."""

from __future__ import annotations

import math
import os
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from app.config import BASE_DIR
from app.control import (
    DEFAULT_CONTROL_PATH,
    ControlState,
    read_control,
    write_control,
)
from app.scrap import (
    DEFAULT_SCRAP_PATH,
    ScrapPrices,
    default_scrap_prices,
    load_scrap_prices,
    save_scrap_prices,
)
from app.status import DEFAULT_STATUS_PATH, is_alive, read_status

CONTROL_PATH = Path(os.getenv("CONTROL_PATH") or DEFAULT_CONTROL_PATH)
STATUS_PATH = Path(os.getenv("STATUS_PATH") or DEFAULT_STATUS_PATH)
SCRAP_PATH = Path(os.getenv("SCRAP_PATH") or DEFAULT_SCRAP_PATH)
LOG_PATH = Path(os.getenv("LOG_PATH") or (BASE_DIR / "logs" / "app.log"))
LOG_TAIL_LINES = 15
PURITY_LIST = (999, 958, 925, 916, 900, 875, 830, 800, 750, 500)
NO_HALLMARK_OPTIONS = (800, 875, 925)


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
        self.scrap_window: ScrapPricesWindow | None = None

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
        ttk.Button(buttons, text="Обновить", command=self.refresh).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(
            buttons, text="Цены лома", command=self.open_scrap_editor
        ).pack(side="left")

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

    def open_scrap_editor(self) -> "ScrapPricesWindow":
        """Открыть окно цен лома, не блокируя mainloop."""
        if (
            self.scrap_window is not None
            and self.scrap_window.window.winfo_exists()
        ):
            self.scrap_window.window.lift()
            self.scrap_window.window.focus_force()
            return self.scrap_window
        self.scrap_window = ScrapPricesWindow(self.root, SCRAP_PATH)
        return self.scrap_window

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


class ScrapPricesWindow:
    """Окно редактирования цен лома по пробам."""

    def __init__(self, parent: tk.Misc, path: str | Path) -> None:
        self.path = Path(path)
        self.data = load_scrap_prices(self.path)
        self.entries: dict[int, tk.StringVar] = {}
        self.no_hallmark_var = tk.StringVar()
        self.status_var = tk.StringVar(value="")

        self.window = tk.Toplevel(parent)
        self.window.title("Цены лома, BYN/г")
        self.window.geometry("420x460")
        self.window.minsize(400, 420)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._fill(self.data)

    def _build(self) -> None:
        """Собрать поля и кнопки."""
        title_font = tkfont.nametofont("TkDefaultFont").copy()
        title_font.configure(size=13, weight="bold")
        tk.Label(
            self.window, text="Цены лома, BYN/г", font=title_font
        ).pack(anchor="w", padx=14, pady=(12, 6))

        grid = ttk.LabelFrame(self.window, text="Цены по пробам")
        grid.pack(fill="x", padx=12, pady=4)
        for row, purity in enumerate(PURITY_LIST):
            ttk.Label(grid, text=str(purity)).grid(
                row=row, column=0, sticky="w", padx=(10, 6), pady=2
            )
            variable = tk.StringVar()
            self.entries[purity] = variable
            ttk.Entry(grid, textvariable=variable, width=10).grid(
                row=row, column=1, sticky="w", pady=2
            )
            ttk.Label(grid, text="BYN/г").grid(
                row=row, column=2, sticky="w", padx=(6, 10), pady=2
            )

        hall_frame = ttk.Frame(self.window)
        hall_frame.pack(fill="x", padx=12, pady=(8, 4))
        ttk.Label(
            hall_frame, text="925 без клейма считать как"
        ).pack(side="left")
        ttk.Combobox(
            hall_frame,
            textvariable=self.no_hallmark_var,
            values=[str(value) for value in NO_HALLMARK_OPTIONS],
            state="readonly",
            width=5,
        ).pack(side="left", padx=(8, 4))
        ttk.Label(hall_frame, text="проба").pack(side="left")

        ttk.Label(self.window, textvariable=self.status_var).pack(
            anchor="w", padx=16, pady=(8, 4)
        )

        actions = ttk.Frame(self.window)
        actions.pack(fill="x", padx=12, pady=(0, 12))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="Сохранить", command=self.save).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(actions, text="Закрыть", command=self.close).grid(
            row=0, column=1, sticky="e"
        )
        ttk.Button(
            actions,
            text="Сбросить к значениям по умолчанию",
            command=self.reset,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _fill(self, data: ScrapPrices) -> None:
        """Показать значения из файла/по умолчанию."""
        for purity, variable in self.entries.items():
            value = data.prices.get(purity)
            variable.set("" if value is None else _format_price(value))
        no_hallmark = data.no_hallmark_purity
        if no_hallmark not in NO_HALLMARK_OPTIONS:
            no_hallmark = 800
        self.no_hallmark_var.set(str(no_hallmark))

    def save(self) -> bool:
        """Проверить поля и атомарно сохранить цены."""
        prices: dict[int, float] = {}
        try:
            for purity, variable in self.entries.items():
                raw = variable.get().strip().replace(",", ".")
                if not raw:
                    continue
                value = float(raw)
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(
                        f"цена для пробы {purity} должна быть больше 0"
                    )
                prices[purity] = value
            no_hallmark = int(self.no_hallmark_var.get())
            if no_hallmark not in NO_HALLMARK_OPTIONS:
                raise ValueError(
                    "925 без клейма: выберите 800, 875 или 925"
                )
            save_scrap_prices(
                self.path,
                ScrapPrices(
                    prices=prices, no_hallmark_purity=no_hallmark
                ),
            )
        except (ValueError, OSError) as exc:
            messagebox.showerror(
                "Цены лома",
                f"Не удалось сохранить: {exc}",
                parent=self.window,
            )
            return False

        self.status_var.set(f"Сохранено {datetime.now().strftime('%H:%M:%S')}")
        return True

    def reset(self) -> None:
        """Заполнить поля значениями по умолчанию, не сохраняя файл."""
        self._fill(default_scrap_prices())
        self.status_var.set(
            "Значения по умолчанию — нажмите «Сохранить»"
        )

    def close(self) -> None:
        """Закрыть окно."""
        self.window.destroy()


def _format_price(value: float) -> str:
    """Показать цену с запятой и без лишних нулей."""
    return f"{value:g}".replace(".", ",")


def main() -> None:
    """Запустить окно."""
    root = tk.Tk()
    SilverCoockoldUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
