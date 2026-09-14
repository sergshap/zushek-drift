# -*- coding: utf-8 -*-
"""
«Зушек Дрифт» — десктопное приложение для детского отдела развлечений.
Система учёта клиентов и продаж.

Запуск:  python main.py
"""

import os
import sys
import json
import shutil
import logging
import tempfile
import subprocess
import traceback
import urllib.request
from datetime import datetime, date, timedelta

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from database import DatabaseManager, BASE_DIR
from services import (
    ClientService, SubscriptionService, DirectoryService, ReportService,
    format_phone, format_phone_input, format_date_input, is_valid_phone,
    parse_date, fmt_date,
)

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join(BASE_DIR, "app.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("zushek")

# ---------------------------------------------------------------------------
# НАСТРОЙКИ АВТООБНОВЛЕНИЯ
# ---------------------------------------------------------------------------
# ВАЖНО: замените на свой репозиторий в формате "владелец/репозиторий"
GITHUB_REPO = "sergshap/zushek-drift"          # <-- ЗАМЕНИТЕ

# Текущая версия приложения (в формате 1.0 / 1.5 / 2.0.1)
CURRENT_VERSION = "1.6"

# Включить/выключить проверку при запуске
AUTO_UPDATE_CHECK = True


def _parse_version(v: str):
    """Преобразует '1.2.3' или 'v1.2.3' в (1, 2, 3)."""
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except Exception:
        return (0,)


def _is_repo_configured() -> bool:
    """Проверяет, что пользователь задал свой репозиторий."""
    if not GITHUB_REPO:
        return False
    if "ваш-логин" in GITHUB_REPO.lower():
        return False
    if "/" not in GITHUB_REPO:
        return False
    return True


def _fetch_latest_release():
    """Возвращает dict релиза или None при ошибке."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ZushekDrift-Updater",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except Exception as e:
        logger.warning("Не удалось получить релиз с GitHub: %s", e)
        return None


def _download_file(url: str, target_path: str, timeout: int = 60):
    """Скачивает файл по URL в target_path."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "ZushekDrift-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, \
            open(target_path, "wb") as f:
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            f.write(chunk)


def check_for_updates(parent=None, silent_if_no_update=True) -> bool:
    """
    Проверяет обновления на GitHub.

    parent                 — окно-родитель (для диалогов). Если None — создаётся
                             временный скрытый root.
    silent_if_no_update    — не показывать сообщение, если обновлений нет.

    Возвращает True, если обновление было успешно установлено (в этом случае
    процесс уже будет перезапущен — обычно сюда управление не возвращается).
    """
    if not _is_repo_configured():
        logger.info("Автопроверка обновлений пропущена: "
                    "GITHUB_REPO не настроен (см. main.py)")
        if not silent_if_no_update:
            messagebox.showinfo(
                "Обновления",
                "Автопроверка обновлений не настроена.\n\n"
                "Откройте main.py и укажите свой репозиторий в GITHUB_REPO.",
                parent=parent)
        return False

    own_root = False
    if parent is None:
        parent = tk.Tk()
        parent.withdraw()
        own_root = True

    try:
        data = _fetch_latest_release()
        if not data:
            if not silent_if_no_update:
                messagebox.showwarning(
                    "Обновления",
                    "Не удалось проверить обновления.\n"
                    "Проверьте подключение к интернету.",
                    parent=parent)
            return False

        tag = (data.get("tag_name") or "").lstrip("v")
        if not tag:
            return False

        if _parse_version(tag) <= _parse_version(CURRENT_VERSION):
            logger.info("Обновлений нет (текущая %s, последняя %s)",
                        CURRENT_VERSION, tag)
            if not silent_if_no_update:
                messagebox.showinfo(
                    "Обновления",
                    f"У вас последняя версия: {CURRENT_VERSION}",
                    parent=parent)
            return False

        # --- Есть новая версия ---
        notes = (data.get("body") or "Нет описания.").strip()
        if len(notes) > 800:
            notes = notes[:800] + "..."
        assets = data.get("assets") or []

        # Ищем .py / .exe / .zip
        chosen = None
        for a in assets:
            name = (a.get("name") or "").lower()
            if name.endswith((".py", ".exe", ".zip")):
                chosen = a
                break
        if not chosen and assets:
            chosen = assets[0]

        msg = (
            f"Доступна новая версия!\n\n"
            f"Текущая: {CURRENT_VERSION}\n"
            f"Новая:   {tag}\n\n"
            f"Что нового:\n{notes}\n\n"
            f"Обновить сейчас?"
        )
        if not messagebox.askyesno("Обновление Зушек Дрифт", msg, parent=parent):
            logger.info("Пользователь отклонил обновление")
            return False

        if not chosen:
            messagebox.showwarning(
                "Обновление",
                "В релизе не найден файл обновления (.py/.exe/.zip).\n"
                "Скачайте вручную со страницы релиза на GitHub.",
                parent=parent)
            return False

        url = chosen.get("browser_download_url")
        fname = chosen.get("name") or "update.py"
        if not url:
            messagebox.showerror("Ошибка",
                                 "Не удалось получить ссылку на файл.",
                                 parent=parent)
            return False

        tmp_path = os.path.join(tempfile.gettempdir(), fname)
        logger.info("Скачивание обновления: %s -> %s", url, tmp_path)
        try:
            _download_file(url, tmp_path)
        except Exception as e:
            logger.exception("Ошибка скачивания обновления")
            messagebox.showerror("Ошибка",
                                 f"Не удалось скачать обновление:\n{e}",
                                 parent=parent)
            return False

        # --- Установка ---
        low = fname.lower()
        if low.endswith(".py"):
            current_file = os.path.abspath(sys.argv[0])
            backup = current_file + ".bak"

            # Проверим, что запущенный файл — действительно .py
            if not current_file.lower().endswith(".py"):
                messagebox.showwarning(
                    "Обновление",
                    f"Новая версия скачана в:\n{tmp_path}\n\n"
                    f"Приложение запущено не из .py файла. Замените вручную.",
                    parent=parent)
                return False

            try:
                if os.path.exists(backup):
                    os.remove(backup)
                shutil.copy2(current_file, backup)
                shutil.copy2(tmp_path, current_file)
                logger.info("Обновление установлено, бэкап: %s", backup)
            except Exception as e:
                logger.exception("Ошибка замены файла")
                messagebox.showerror(
                    "Ошибка",
                    f"Не удалось заменить файл:\n{e}\n\n"
                    f"Новая версия сохранена здесь: {tmp_path}",
                    parent=parent)
                return False

            messagebox.showinfo(
                "Обновление установлено",
                f"Обновление до версии {tag} установлено.\n"
                f"Приложение будет перезапущено.",
                parent=parent)

            # Перезапуск
            try:
                python_exe = sys.executable or "python"
                subprocess.Popen(
                    [python_exe, current_file] + sys.argv[1:],
                    cwd=os.path.dirname(current_file) or None,
                )
            except Exception:
                logger.exception("Не удалось перезапустить приложение")

            if own_root:
                try:
                    parent.destroy()
                except Exception:
                    pass
            sys.exit(0)

        elif low.endswith(".exe"):
            # .exe нельзя заменить, пока он запущен
            messagebox.showinfo(
                "Обновление",
                f"Новая версия скачана:\n{tmp_path}\n\n"
                f"Закройте приложение и вручную замените .exe файл.",
                parent=parent)
            return False

        else:
            messagebox.showinfo(
                "Обновление",
                f"Файл обновления скачан:\n{tmp_path}\n\n"
                f"Установите его вручную.",
                parent=parent)
            return False

    except Exception:
        logger.exception("Ошибка проверки обновлений")
        return False
    finally:
        if own_root:
            try:
                parent.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Палитра
# ---------------------------------------------------------------------------
C = {
    "yellow":     "#FFD93D",
    "yellow_dk":  "#F4C430",
    "blue":       "#4D96FF",
    "blue_lt":    "#D6E8FF",
    "orange":     "#FF8C42",
    "orange_lt":  "#FFE3CC",
    "green":      "#4CAF50",
    "red":        "#E74C3C",
    "bg":         "#FFFDF5",
    "bg2":        "#F4F8FF",
    "text":       "#22303F",
    "gray":       "#8A94A6",
}

DEFAULT_DURATION_MIN = 10


def apply_style(root: tk.Tk):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=("Segoe UI", 10), background=C["bg"],
                    foreground=C["text"])
    style.configure("TFrame", background=C["bg"])
    style.configure("Card.TFrame", background=C["bg2"], relief="flat")
    style.configure("TLabel", background=C["bg"], foreground=C["text"])
    style.configure("Card.TLabel", background=C["bg2"], foreground=C["text"])
    style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"),
                    foreground=C["orange"], background=C["bg"])
    style.configure("Sub.TLabel", font=("Segoe UI", 10),
                    foreground=C["gray"], background=C["bg"])
    style.configure("Big.TLabel", font=("Segoe UI", 14, "bold"),
                    foreground=C["blue"], background=C["bg2"])
    style.configure("TButton", font=("Segoe UI", 10), padding=6)
    style.configure("Accent.TButton", background=C["blue"], foreground="white",
                    font=("Segoe UI", 10, "bold"), padding=8)
    style.map("Accent.TButton",
              background=[("active", C["orange"]), ("!disabled", C["blue"])],
              foreground=[("active", "white")])
    style.configure("Warn.TButton", background=C["orange"], foreground="white",
                    font=("Segoe UI", 10, "bold"), padding=8)
    style.map("Warn.TButton", background=[("active", C["yellow_dk"])])
    style.configure("Danger.TButton", background=C["red"], foreground="white")
    style.map("Danger.TButton", background=[("active", "#C0392B")])
    style.configure("TNotebook", background=C["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", font=("Segoe UI", 11, "bold"),
                    padding=(16, 8), background=C["bg2"], foreground=C["text"])
    style.map("TNotebook.Tab",
              background=[("selected", C["yellow"])],
              foreground=[("selected", C["text"])])
    style.configure("Treeview", font=("Segoe UI", 10), rowheight=26,
                    background="white", fieldbackground="white")
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"),
                    background=C["blue"], foreground="white")
    style.map("Treeview.Heading", background=[("active", C["orange"])])
    style.configure("TLabelframe", background=C["bg"], bordercolor=C["blue"])
    style.configure("TLabelframe.Label", background=C["bg"],
                    foreground=C["blue"], font=("Segoe UI", 10, "bold"))
    style.configure("TCheckbutton", background=C["bg"])
    style.configure("Card.TCheckbutton", background=C["bg2"])
    style.configure("TEntry", fieldbackground="white")
    style.configure("TCombobox", fieldbackground="white")


# ===========================================================================
# Утилиты загрузки логотипа (локально и по URL)
# ===========================================================================
def _download_logo(url: str):
    """Скачивает картинку по URL во временный файл, возвращает путь."""
    url_lower = url.lower().split("?")[0]
    ext = ".png"
    if url_lower.endswith((".jpg", ".jpeg")):
        ext = ".jpg"
    elif url_lower.endswith(".gif"):
        ext = ".gif"
    elif url_lower.endswith(".png"):
        ext = ".png"
    cache_path = os.path.join(BASE_DIR, "logo_cache" + ext)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (ZushekDrift)"})
        with urllib.request.urlopen(req, timeout=10) as resp, \
                open(cache_path, "wb") as f:
            f.write(resp.read())
        logger.info("Логотип скачан: %s", cache_path)
        return cache_path
    except Exception:
        logger.exception("Не удалось скачать логотип из %s", url)
        return None


def _resolve_logo(path_or_url: str):
    """Возвращает локальный путь к логотипу (по URL — скачивает)."""
    if not path_or_url:
        return None
    s = path_or_url.strip()
    if s.startswith(("http://", "https://")):
        return _download_logo(s)
    if os.path.isfile(s):
        return s
    return None


def make_logo_image(path: str, max_height: int = 40):
    """Возвращает PhotoImage из файла. Для JPG/GIF — через Pillow, если есть."""
    if not path or not os.path.isfile(path):
        return None
    try:
        from PIL import Image, ImageTk
        img = Image.open(path)
        w, h = img.size
        if h > max_height and h > 0:
            ratio = max_height / h
            img = img.resize((max(1, int(w * ratio)), max_height),
                             Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except ImportError:
        pass
    except Exception:
        logger.exception("Pillow не открыл %s", path)
    try:
        img = tk.PhotoImage(file=path)
        w, h = img.width(), img.height()
        if h > max_height and h > 0:
            factor = max(1, int(round(h / max_height)))
            if factor > 1:
                img = img.subsample(factor, factor)
        return img
    except Exception:
        logger.exception("Tk не открыл изображение %s", path)
        return None


# ===========================================================================
# Вспомогательные функции для отображения
# ===========================================================================
def _age_word(n: int) -> str:
    """Склонение слова «год»: 1 год, 2 года, 5 лет."""
    n10, n100 = n % 10, n % 100
    if 11 <= n100 <= 14:
        return "лет"
    if n10 == 1:
        return "год"
    if 2 <= n10 <= 4:
        return "года"
    return "лет"


def _human_age(birth_date) -> str:
    """Возвращает строку возраста: «5 лет», «1 год», «23 года»."""
    bd = parse_date(birth_date) if isinstance(birth_date, str) else birth_date
    if not bd:
        return ""
    today = date.today()
    years = today.year - bd.year - (
        (today.month, today.day) < (bd.month, bd.day))
    if years < 0:
        return ""
    return f"{years} {_age_word(years)}"


def _human_last_visit(db, client_id: int) -> str:
    """Возвращает человекочитаемую строку последнего визита:
    «сегодня, 12:34» / «вчера, 15:40» / «14-09-2026, 18:05» / «не было визитов»."""
    row = db.query_one(
        "SELECT session_date, start_time FROM sessions "
        "WHERE client_id=? AND is_deleted=0 "
        "ORDER BY session_date DESC, start_time DESC LIMIT 1",
        (client_id,)
    )
    if not row or not row["session_date"]:
        return "не было визитов"
    d = parse_date(row["session_date"])
    if not d:
        return str(row["session_date"])
    t = (row["start_time"] or "").strip()
    today = date.today()
    delta = (today - d).days
    if delta == 0:
        label = "сегодня"
    elif delta == 1:
        label = "вчера"
    else:
        label = fmt_date(d)
    return f"{label}, {t}" if t else label


# ===========================================================================
# МОДАЛЬНЫЕ ДИАЛОГИ
# ===========================================================================
class ClientDialog(tk.Toplevel):
    """Создание/редактирование клиента."""

    def __init__(self, master, app, client_id=None):
        super().__init__(master)
        self.app = app
        self.client_id = client_id
        self.title("Новый клиент" if not client_id else "Карточка клиента")
        self.configure(bg=C["bg"])
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.var_fio = tk.StringVar()
        self.var_phone = tk.StringVar()
        self.var_birth = tk.StringVar()
        self.var_notes = tk.StringVar()

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="ФИО *").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_fio, width=36).grid(
            row=0, column=1, pady=4, padx=6)

        ttk.Label(frm, text="Телефон").grid(row=1, column=0, sticky="w", pady=4)
        e_phone = ttk.Entry(frm, textvariable=self.var_phone, width=36)
        e_phone.grid(row=1, column=1, pady=4, padx=6)
        e_phone.bind("<KeyRelease>", self._on_phone_key)
        self._e_phone = e_phone
        ttk.Label(frm, text="+7(XXX)XXX-XX-XX", style="Sub.TLabel").grid(
            row=1, column=2, sticky="w")

        ttk.Label(frm, text="Дата рождения").grid(row=2, column=0, sticky="w",
                                                  pady=4)
        e_birth = ttk.Entry(frm, textvariable=self.var_birth, width=36)
        e_birth.grid(row=2, column=1, pady=4, padx=6)
        e_birth.bind("<KeyRelease>", self._on_birth_key)
        self._e_birth = e_birth
        ttk.Label(frm, text="ДД-ММ-ГГГГ", style="Sub.TLabel").grid(
            row=2, column=2, sticky="w")

        ttk.Label(frm, text="Заметки").grid(row=3, column=0, sticky="nw", pady=4)
        ttk.Entry(frm, textvariable=self.var_notes, width=36).grid(
            row=3, column=1, pady=4, padx=6)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=3, pady=(14, 0), sticky="e")
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(
            side="right", padx=4)
        ttk.Button(btns, text="Сохранить", style="Accent.TButton",
                   command=self.save).pack(side="right")

        if client_id:
            c = app.client_service.get(client_id)
            if c:
                self.var_fio.set(c["fio"] or "")
                self.var_phone.set(c["phone"] or "")
                self.var_birth.set(fmt_date(c["birth_date"]))
                self.var_notes.set(c["notes"] or "")

        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self._center(master)

    def _on_phone_key(self, event):
        raw = self.var_phone.get()
        formatted = format_phone_input(raw)
        if formatted != raw:
            self.var_phone.set(formatted)
            self._e_phone.icursor("end")

    def _on_birth_key(self, event):
        raw = self.var_birth.get()
        formatted = format_date_input(raw)
        if formatted != raw:
            self.var_birth.set(formatted)
            self._e_birth.icursor("end")

    def _center(self, master):
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x,0)}+{max(y,0)}")

    def save(self):
        fio = self.var_fio.get().strip()
        phone = self.var_phone.get().strip()
        birth_raw = self.var_birth.get().strip()
        notes = self.var_notes.get().strip()

        if not fio:
            messagebox.showwarning("Внимание", "Укажите ФИО клиента", parent=self)
            return
        if phone and not is_valid_phone(phone):
            messagebox.showwarning(
                "Внимание",
                "Некорректный телефон. Введите 11 цифр", parent=self)
            return
        if phone:
            phone = format_phone(phone)
        birth = ""
        if birth_raw:
            bd = parse_date(birth_raw)
            if not bd:
                messagebox.showwarning(
                    "Внимание",
                    "Дата рождения в формате ДД-ММ-ГГГГ", parent=self)
                return
            birth = str(bd)

        try:
            if self.client_id:
                self.app.client_service.update(self.client_id, fio, phone,
                                               birth, notes)
            else:
                self.client_id = self.app.client_service.create(
                    fio, phone, birth, notes)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {e}",
                                 parent=self)
            return
        self.destroy()


class ClientPickerDialog(tk.Toplevel):
    """Поиск и выбор клиента."""

    def __init__(self, master, app, on_select):
        super().__init__(master)
        self.app = app
        self.on_select = on_select
        self.title("Поиск клиента")
        self.configure(bg=C["bg"])
        self.geometry("780x520")
        self.transient(master)
        self.grab_set()

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Поиск (ФИО / телефон / № абонемента):").pack(side="left")
        self.var_q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.var_q, width=40)
        e.pack(side="left", padx=6)
        e.bind("<Return>", lambda ev: self.do_search())
        ttk.Button(top, text="Найти", style="Accent.TButton",
                   command=self.do_search).pack(side="left")
        ttk.Button(top, text="Новый клиент",
                   command=self.new_client).pack(side="left", padx=6)

        cols = ("id", "fio", "phone", "birth", "age", "visits")
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c, t, w in zip(cols,
                           ("ID", "ФИО", "Телефон", "Дата рождения",
                            "Возраст", "Визитов"),
                           (50, 260, 140, 120, 90, 80)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", lambda e: self.choose())

        ttk.Button(self, text="Выбрать", style="Accent.TButton",
                   command=self.choose).pack(pady=(0, 10))

        self.do_search()
        e.focus_set()
        self.bind("<Escape>", lambda ev: self.destroy())

    def do_search(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.app.client_service.search(self.var_q.get())
        for r in rows:
            st = self.app.client_service.stats(r["id"])
            self.tree.insert("", "end", iid=str(r["id"]),
                             values=(r["id"], r["fio"], r["phone"] or "",
                                     fmt_date(r["birth_date"]),
                                     _human_age(r["birth_date"]),
                                     st["visits"]))

    def new_client(self):
        dlg = ClientDialog(self, self.app)
        self.wait_window(dlg)
        if dlg.client_id:
            self.on_select(dlg.client_id)
            self.destroy()

    def choose(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.on_select(int(sel[0]))
        self.destroy()


class VisitEditDialog(tk.Toplevel):
    """Редактирование / добавление визита в модальном окне."""

    def __init__(self, master, app, session_id=None, on_saved=None):
        super().__init__(master)
        self.title("Редактирование визита" if session_id else "Новый визит")
        self.configure(bg=C["bg"])
        self.geometry("980x740")
        self.transient(master)
        self.grab_set()

        self.form = VisitForm(self, app, session_id=session_id,
                              on_saved=self._on_saved, is_dialog=True)
        self.form.pack(fill="both", expand=True)

        bottom = ttk.Frame(self, padding=(12, 6))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Закрыть", command=self.destroy).pack(
            side="right")
        self._external_on_saved = on_saved

    def _on_saved(self):
        if self._external_on_saved:
            try:
                self._external_on_saved()
            except Exception:
                logger.exception("Ошибка callback on_saved")


# ===========================================================================
# УНИВЕРСАЛЬНАЯ ФОРМА ВИЗИТА
# ===========================================================================
class VisitForm(ttk.Frame):
    """Форма создания/редактирования визита."""

    def __init__(self, master, app, session_id=None, on_saved=None,
                 is_dialog=False):
        super().__init__(master, padding=12)
        self.app = app
        self.db = app.db
        self.session_id = session_id
        self.on_saved = on_saved
        self.is_dialog = is_dialog
        self.client_id = None
        self._saved_base = None
        self._sub_map = {}

        self.var_search = tk.StringVar()
        self.var_client = tk.StringVar(value="— клиент не выбран —")
        self.var_attraction = tk.StringVar()
        self.var_unit = tk.StringVar()
        self.var_date = tk.StringVar(value=fmt_date(date.today()))
        self.var_start = tk.StringVar()
        self.var_end = tk.StringVar()
        self.var_duration = tk.StringVar(value=str(DEFAULT_DURATION_MIN))
        self.var_base = tk.StringVar(value="0")
        self.var_discount_on = tk.BooleanVar(value=False)
        self.var_discount = tk.StringVar()
        self.var_discount_pct = tk.StringVar(value="0")
        self.var_final = tk.StringVar(value="0")
        self.var_payment = tk.StringVar()
        self.var_subscription = tk.StringVar()
        self.var_sub_count = tk.StringVar(value="1")
        self.var_operator = tk.StringVar(value=app.db.current_user)
        self.var_comment = tk.StringVar()

        self._build()
        self.reload_directories()

        if session_id:
            self._load_session(session_id)
        else:
            self._set_now()

    # ------------------------------------------------------------------
    def _build(self):
        self.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(self,
                  text="РЕДАКТИРОВАНИЕ ВИЗИТА" if self.session_id else "НОВЫЙ ВИЗИТ",
                  style="Header.TLabel").grid(row=row, column=0, columnspan=4,
                                              sticky="w", pady=(0, 8))
        row += 1

        # ---- Клиент ----
        box_c = ttk.LabelFrame(self, text="Клиент", padding=8)
        box_c.grid(row=row, column=0, columnspan=4, sticky="ew", pady=4)
        box_c.columnconfigure(3, weight=1)

        ttk.Label(box_c, text="Поиск:").grid(row=0, column=0, padx=4)
        e_search = ttk.Entry(box_c, textvariable=self.var_search, width=32)
        e_search.grid(row=0, column=1, padx=4)
        e_search.bind("<Return>", lambda e: self.quick_search())
        ttk.Button(box_c, text="Найти", command=self.quick_search).grid(
            row=0, column=2, padx=4)
        ttk.Button(box_c, text="Выбрать из списка",
                   command=self.pick_client).grid(row=0, column=3, padx=4,
                                                  sticky="w")
        ttk.Button(box_c, text="Новый клиент",
                   command=self.new_client).grid(row=0, column=4, padx=4)

        ttk.Label(box_c, textvariable=self.var_client, style="Big.TLabel",
                  justify="left").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        row += 1

        # ---- Услуга / техника / время ----
        box_s = ttk.LabelFrame(self, text="Услуга и время", padding=8)
        box_s.grid(row=row, column=0, columnspan=4, sticky="ew", pady=4)
        box_s.columnconfigure(1, weight=1)
        box_s.columnconfigure(3, weight=1)

        ttk.Label(box_s, text="Услуга / товар:").grid(row=0, column=0,
                                                      sticky="w", pady=3)
        self.cmb_attraction = ttk.Combobox(box_s, textvariable=self.var_attraction,
                                           state="readonly", width=30)
        self.cmb_attraction.grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        self.cmb_attraction.bind("<<ComboboxSelected>>",
                                 lambda e: self._on_attraction_change())

        ttk.Label(box_s, text="Номер (карт/машинка/кресло):").grid(
            row=0, column=2, sticky="w", pady=3)
        self.cmb_unit = ttk.Combobox(box_s, textvariable=self.var_unit, width=18)
        self.cmb_unit.grid(row=0, column=3, sticky="ew", padx=6, pady=3)

        ttk.Label(box_s, text="Дата (ДД-ММ-ГГГГ):").grid(row=1, column=0,
                                                         sticky="w", pady=3)
        self.e_date = ttk.Entry(box_s, textvariable=self.var_date, width=14)
        self.e_date.grid(row=1, column=1, sticky="w", padx=6, pady=3)
        self.e_date.bind("<KeyRelease>", self._on_date_key)

        ttk.Label(box_s, text="Начало / Конец:").grid(row=1, column=2, sticky="w")
        tframe = ttk.Frame(box_s)
        tframe.grid(row=1, column=3, sticky="ew", padx=6)
        self.e_start = ttk.Entry(tframe, textvariable=self.var_start, width=9)
        self.e_start.pack(side="left")
        self.e_start.bind("<KeyRelease>", lambda e: self._recalc_end_time())
        ttk.Label(tframe, text=" — ").pack(side="left")
        self.e_end = ttk.Entry(tframe, textvariable=self.var_end, width=9)
        self.e_end.pack(side="left")

        ttk.Label(box_s, text="Длительность (мин):").grid(row=2, column=0,
                                                          sticky="w", pady=3)
        self.e_dur = ttk.Entry(box_s, textvariable=self.var_duration, width=8)
        self.e_dur.grid(row=2, column=1, sticky="w", padx=6, pady=3)
        self.e_dur.bind("<KeyRelease>", lambda e: self._recalc_end_time())

        ttk.Label(box_s,
                  text="Конец рассчитывается автоматически: начало + длительность",
                  style="Sub.TLabel").grid(row=2, column=2, columnspan=2,
                                           sticky="w", padx=6)
        row += 1

        # ---- Оплата ----
        box_p = ttk.LabelFrame(self, text="Оплата", padding=8)
        box_p.grid(row=row, column=0, columnspan=4, sticky="ew", pady=4)
        box_p.columnconfigure(1, weight=1)
        box_p.columnconfigure(3, weight=1)

        ttk.Label(box_p, text="Базовая цена:").grid(row=0, column=0, sticky="w",
                                                    pady=3)
        self.e_base = ttk.Entry(box_p, textvariable=self.var_base, width=14)
        self.e_base.grid(row=0, column=1, sticky="w", padx=6, pady=3)
        self.e_base.bind("<KeyRelease>", lambda e: self.recalc())

        ttk.Label(box_p, text="Формат оплаты:").grid(row=0, column=2, sticky="w")
        self.cmb_payment = ttk.Combobox(box_p, textvariable=self.var_payment,
                                        state="readonly", width=20)
        self.cmb_payment.grid(row=0, column=3, sticky="ew", padx=6, pady=3)
        self.cmb_payment.bind("<<ComboboxSelected>>",
                              lambda e: self._on_payment_change())

        self.chk_discount = ttk.Checkbutton(
            box_p, text="Со скидкой", variable=self.var_discount_on,
            command=self._on_discount_toggle)
        self.chk_discount.grid(row=1, column=0, sticky="w", pady=3)

        self.cmb_discount = ttk.Combobox(box_p, textvariable=self.var_discount,
                                         state="disabled", width=32)
        self.cmb_discount.grid(row=1, column=1, sticky="ew", padx=6, pady=3)
        self.cmb_discount.bind("<<ComboboxSelected>>",
                               lambda e: self._apply_discount())

        ttk.Label(box_p, text="Скидка, %:").grid(row=1, column=2, sticky="w")
        self.e_pct = ttk.Entry(box_p, textvariable=self.var_discount_pct, width=10)
        self.e_pct.grid(row=1, column=3, sticky="w", padx=6, pady=3)
        self.e_pct.bind("<KeyRelease>", lambda e: self.recalc())

        ttk.Label(box_p, text="Итого к оплате:").grid(row=2, column=0,
                                                      sticky="w", pady=(10, 3))
        ttk.Label(box_p, textvariable=self.var_final, style="Big.TLabel").grid(
            row=2, column=1, sticky="w", padx=6)

        ttk.Label(box_p, text="Абонемент:").grid(row=3, column=0, sticky="w")
        self.cmb_sub = ttk.Combobox(box_p, textvariable=self.var_subscription,
                                    state="disabled", width=40)
        self.cmb_sub.grid(row=3, column=1, columnspan=3, sticky="ew",
                          padx=6, pady=3)

        self.lbl_sub_count = ttk.Label(box_p, text="Списать сеансов:")
        self.lbl_sub_count.grid(row=4, column=0, sticky="w", pady=(3, 0))
        self.cmb_sub_count = ttk.Combobox(
            box_p, textvariable=self.var_sub_count, state="disabled",
            width=6, values=[str(i) for i in range(1, 21)])
        self.cmb_sub_count.grid(row=4, column=1, sticky="w",
                                padx=6, pady=(3, 0))
        ttk.Label(box_p,
                  text="сколько сеансов списать с абонемента за этот визит",
                  style="Sub.TLabel").grid(
            row=4, column=2, columnspan=2, sticky="w", pady=(3, 0))
        row += 1

        # ---- Оператор / комментарий ----
        box_o = ttk.LabelFrame(self, text="Дополнительно", padding=8)
        box_o.grid(row=row, column=0, columnspan=4, sticky="ew", pady=4)
        box_o.columnconfigure(1, weight=1)

        ttk.Label(box_o, text="Оператор:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(box_o, textvariable=self.var_operator, width=25).grid(
            row=0, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(box_o, text="Комментарий:").grid(row=1, column=0, sticky="w")
        ttk.Entry(box_o, textvariable=self.var_comment, width=60).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        row += 1

        btns = ttk.Frame(self)
        btns.grid(row=row, column=0, columnspan=4, pady=12, sticky="e")
        if not self.is_dialog:
            ttk.Button(btns, text="Очистить", command=self.clear).pack(
                side="left", padx=4)
        ttk.Button(btns, text="Сохранить визит", style="Accent.TButton",
                   command=self.save).pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # Ввод даты
    # ------------------------------------------------------------------
    def _on_date_key(self, event):
        raw = self.var_date.get()
        formatted = format_date_input(raw)
        if formatted != raw:
            self.var_date.set(formatted)
            self.e_date.icursor("end")

    # ------------------------------------------------------------------
    # Справочники
    # ------------------------------------------------------------------
    def reload_directories(self):
        self.attractions = self.app.directory_service.attractions_active()
        self._att_map = {a["name"]: a["id"] for a in self.attractions}
        self.cmb_attraction["values"] = list(self._att_map.keys())

        self.payments = self.app.directory_service.payment_types_active()
        self.discounts = self.app.directory_service.discounts_active()
        self._disc_map = {f"{d['name']} ({d['percent']}%)": d
                          for d in self.discounts}
        self.cmb_discount["values"] = list(self._disc_map.keys())

        self._refresh_payment_choices()

    def _refresh_payment_choices(self):
        """Заполняет combobox оплат. Убирает 'абонемент' для товаров."""
        is_product = self._is_product()
        values = []
        self._pay_map = {}
        for p in self.payments:
            name = p["name"]
            if is_product and "абонемент" in name.lower():
                continue
            values.append(name)
            self._pay_map[name] = p["id"]
        self.cmb_payment["values"] = values
        if self.var_payment.get() and self.var_payment.get() not in self._pay_map:
            if values:
                self.cmb_payment.current(0)
                self._on_payment_change()
            else:
                self.var_payment.set("")

    def _is_product(self) -> bool:
        aid = self._att_map.get(self.var_attraction.get())
        if not aid:
            return False
        for a in self.attractions:
            if a["id"] == aid:
                try:
                    return bool(a["is_product"])
                except (KeyError, IndexError):
                    return False
        return False

    def _is_subscription_payment(self):
        return "абонемент" in self.var_payment.get().lower()

    def _on_attraction_change(self):
        name = self.var_attraction.get()
        aid = self._att_map.get(name)
        is_product = self._is_product()

        if aid:
            att = next((a for a in self.attractions if a["id"] == aid), None)
            if att:
                if not self._is_subscription_payment():
                    self.var_base.set(f"{att['default_price']:.2f}")
                if is_product:
                    self.var_duration.set("0")
                else:
                    dur = att["duration_min"] or DEFAULT_DURATION_MIN
                    self.var_duration.set(str(dur))

            if is_product:
                self.cmb_unit["values"] = []
                self.var_unit.set("")
            else:
                units = self.app.directory_service.units_for_attraction(aid)
                self.cmb_unit["values"] = [u["unit_number"] for u in units]
                if units and not self.var_unit.get():
                    self.var_unit.set(units[0]["unit_number"])

        self._refresh_payment_choices()
        if is_product and self._is_subscription_payment():
            if self.cmb_payment["values"]:
                self.cmb_payment.current(0)
            self._on_payment_change()

        self._recalc_end_time()
        self.recalc()

    def _on_payment_change(self):
        is_sub = self._is_subscription_payment()
        if is_sub and self._is_product():
            if self.cmb_payment["values"]:
                self.cmb_payment.current(0)
                return
            self.var_payment.set("")
            is_sub = False

        if is_sub:
            if self._saved_base is None:
                self._saved_base = self.var_base.get()
            self.var_base.set("0")
            self.var_discount_on.set(False)
            self.var_discount.set("")
            self.var_discount_pct.set("0")
            self.cmb_discount.configure(state="disabled")

            if self.client_id:
                subs = self.app.subscription_service.active_for_client(
                    self.client_id)
                self._sub_map = {
                    f"{s['number']} — остаток "
                    f"{s['total_sessions'] - s['used_sessions']}": s["id"]
                    for s in subs
                }
                self.cmb_sub["values"] = list(self._sub_map.keys())
                self.cmb_sub.configure(state="readonly")
                if self.cmb_sub["values"]:
                    self.cmb_sub.current(0)
            else:
                self.cmb_sub.set("")
                self.cmb_sub["values"] = []
                self.cmb_sub.configure(state="disabled")

            self.cmb_sub_count.configure(state="readonly")
            if self.var_sub_count.get() not in self.cmb_sub_count["values"]:
                self.var_sub_count.set("1")
        else:
            if self._saved_base is not None:
                self.var_base.set(self._saved_base)
                self._saved_base = None
            self.cmb_sub.set("")
            self.cmb_sub["values"] = []
            self.cmb_sub.configure(state="disabled")
            self.cmb_sub_count.configure(state="disabled")
            self.var_sub_count.set("1")
            if self.var_discount_on.get():
                self.cmb_discount.configure(state="readonly")
        self.recalc()

    def _on_discount_toggle(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self.var_discount_on.get():
            self.cmb_discount.configure(state="readonly")
            default = self.db.get_setting("default_discount_percent", "0")
            if self.var_discount_pct.get() in ("", "0"):
                self.var_discount_pct.set(default)
        else:
            self.cmb_discount.configure(state="disabled")
            self.var_discount.set("")
            self.var_discount_pct.set("0")
        self.recalc()

    def _apply_discount(self):
        d = self._disc_map.get(self.var_discount.get())
        if d:
            self.var_discount_pct.set(str(d["percent"]))
            self.recalc()

    # ------------------------------------------------------------------
    def recalc(self):
        if self._is_subscription_payment():
            self.var_final.set("0.00")
            return
        try:
            base = float(self.var_base.get().replace(",", ".") or 0)
        except ValueError:
            base = 0.0
        try:
            pct = float(self.var_discount_pct.get().replace(",", ".") or 0)
        except ValueError:
            pct = 0.0
        if not self.var_discount_on.get():
            pct = 0.0
        pct = max(0.0, min(100.0, pct))
        final = round(base * (1 - pct / 100.0), 2)
        self.var_final.set(f"{final:.2f}")

    # ------------------------------------------------------------------
    # Время
    # ------------------------------------------------------------------
    def _set_now(self):
        now = datetime.now()
        self.var_start.set(now.strftime("%H:%M"))
        self.var_date.set(fmt_date(now.date()))
        self.var_duration.set(str(DEFAULT_DURATION_MIN))
        self._recalc_end_time()

    def _recalc_end_time(self):
        start = self.var_start.get().strip()
        try:
            dur = int(self.var_duration.get() or 0)
        except ValueError:
            return
        if not start or ":" not in start:
            return
        try:
            hh, mm = start.split(":")[:2]
            total = int(hh) * 60 + int(mm) + dur
            eh, em = (total // 60) % 24, total % 60
            self.var_end.set(f"{eh:02d}:{em:02d}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Клиент
    # ------------------------------------------------------------------
    def quick_search(self):
        rows = self.app.client_service.search(self.var_search.get())
        if not rows:
            if messagebox.askyesno("Клиент не найден",
                                   "Клиент не найден. Создать нового?"):
                self.new_client()
            return
        if len(rows) == 1:
            self.set_client(rows[0]["id"])
        else:
            self.pick_client()

    def pick_client(self, rows=None):
        dlg = ClientPickerDialog(self, self.app, on_select=self.set_client)
        self.wait_window(dlg)

    def new_client(self):
        dlg = ClientDialog(self, self.app)
        self.wait_window(dlg)
        if dlg.client_id:
            self.set_client(dlg.client_id)

    def set_client(self, client_id):
        c = self.app.client_service.get(client_id)
        if not c:
            return
        self.client_id = client_id
        st = self.app.client_service.stats(client_id)

        age_str = _human_age(c["birth_date"])
        age_part = f"  •  {age_str}" if age_str else ""

        last_visit = _human_last_visit(self.db, client_id)

        self.var_client.set(
            f"{c['fio']}  |  {c['phone'] or '—'}{age_part}\n"
            f"Последний визит: {last_visit}   •   "
            f"визитов: {st['visits']}   •   сумма: {st['total']:.2f}"
        )
        self._on_payment_change()

    # ------------------------------------------------------------------
    # Загрузка / сохранение
    # ------------------------------------------------------------------
    def _load_session(self, sid):
        s = self.db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not s:
            return
        self.set_client(s["client_id"])
        att = self.db.query_one("SELECT name FROM attractions WHERE id=?",
                                (s["attraction_id"],))
        if att:
            self.var_attraction.set(att["name"])
            self._on_attraction_change()
        self.var_unit.set(s["unit_number"] or "")
        self.var_date.set(fmt_date(s["session_date"]) or fmt_date(date.today()))
        self.var_start.set(s["start_time"] or "")
        self.var_end.set(s["end_time"] or "")
        self.var_duration.set(str(s["duration_min"] or DEFAULT_DURATION_MIN))

        pt = self.db.query_one("SELECT name FROM payment_types WHERE id=?",
                               (s["payment_type_id"],))
        if pt:
            self.var_payment.set(pt["name"])
            self._on_payment_change()

        if s["subscription_id"]:
            sub = self.app.subscription_service.get(s["subscription_id"])
            if sub:
                key = (f"{sub['number']} — остаток "
                       f"{sub['total_sessions'] - sub['used_sessions']}")
                self.var_subscription.set(key)
            self.var_sub_count.set("1")
            self.cmb_sub_count.configure(state="readonly")
        else:
            self.var_base.set(f"{s['base_price'] or 0:.2f}")
            pct = s["discount_percent"] or 0
            self.var_discount_on.set(pct > 0)
            self.var_discount_pct.set(str(pct))
            self._on_discount_toggle()

        self.var_final.set(f"{s['final_price'] or 0:.2f}")
        self.var_operator.set(s["operator"] or self.db.current_user)
        self.var_comment.set(s["comment"] or "")

    def clear(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.session_id = None
        self.client_id = None
        self._saved_base = None
        self._sub_map = {}
        self.var_client.set("— клиент не выбран —")
        self.var_search.set("")
        self.var_attraction.set("")
        self.var_unit.set("")
        self.var_duration.set(str(DEFAULT_DURATION_MIN))
        self.var_base.set("0")
        self.var_discount_on.set(False)
        self.var_discount.set("")
        self.var_discount_pct.set("0")
        self.var_final.set("0")
        self.var_payment.set("")
        self.var_subscription.set("")
        self.var_sub_count.set("1")
        self.var_comment.set("")
        self.cmb_sub.set("")
        self.cmb_sub["values"] = []
        self.cmb_sub.configure(state="disabled")
        self.cmb_sub_count.configure(state="disabled")
        self.cmb_discount.configure(state="disabled")
        self._refresh_payment_choices()
        self._set_now()

    def save(self):
        try:
            if not self.client_id:
                messagebox.showwarning("Внимание", "Выберите клиента", parent=self)
                return
            if not self.var_attraction.get():
                messagebox.showwarning("Внимание", "Выберите услугу / товар",
                                       parent=self)
                return
            d = parse_date(self.var_date.get())
            if not d:
                messagebox.showwarning("Внимание",
                                       "Некорректная дата (ДД-ММ-ГГГГ)",
                                       parent=self)
                return

            is_sub = self._is_subscription_payment()
            is_product = self._is_product()
            if is_sub and is_product:
                messagebox.showwarning(
                    "Внимание",
                    "Для товара оплата абонементом недоступна", parent=self)
                return

            try:
                base = 0.0 if is_sub else float(
                    self.var_base.get().replace(",", ".") or 0)
            except ValueError:
                messagebox.showwarning("Внимание", "Цена должна быть числом",
                                       parent=self)
                return
            if not is_sub and base < 0:
                messagebox.showwarning("Внимание",
                                       "Цена не может быть отрицательной",
                                       parent=self)
                return

            try:
                pct = 0.0 if is_sub else float(
                    self.var_discount_pct.get().replace(",", ".") or 0)
            except ValueError:
                pct = 0.0
            if not self.var_discount_on.get() or is_sub:
                pct = 0.0
            pct = max(0.0, min(100.0, pct))
            final = 0.0 if is_sub else round(base * (1 - pct / 100.0), 2)

            att_id = self._att_map.get(self.var_attraction.get())
            pay_id = self._pay_map.get(self.var_payment.get())
            if not pay_id:
                messagebox.showwarning("Внимание", "Выберите формат оплаты",
                                       parent=self)
                return

            # ---- абонемент ----
            sub_id = None
            sub_count = 1
            if is_sub:
                key = self.var_subscription.get()
                if key and hasattr(self, "_sub_map"):
                    sub_id = self._sub_map.get(key)
                if not sub_id:
                    messagebox.showwarning(
                        "Внимание",
                        "Для оплаты абонементом выберите абонемент клиента",
                        parent=self)
                    return
                try:
                    sub_count = int(self.var_sub_count.get() or 1)
                except ValueError:
                    sub_count = 1
                if sub_count < 1:
                    sub_count = 1

                sub = self.app.subscription_service.get(sub_id)
                if not sub:
                    messagebox.showerror("Ошибка",
                                         "Абонемент не найден", parent=self)
                    return
                remaining = (sub["total_sessions"] or 0) - (
                    sub["used_sessions"] or 0)
                if remaining < sub_count:
                    messagebox.showwarning(
                        "Внимание",
                        f"В абонементе недостаточно сеансов.\n"
                        f"Остаток: {remaining}, требуется: {sub_count}",
                        parent=self)
                    return

            try:
                dur = int(self.var_duration.get() or 0)
            except ValueError:
                dur = DEFAULT_DURATION_MIN

            now = datetime.now().isoformat(timespec="seconds")
            data = {
                "client_id": self.client_id,
                "attraction_id": att_id,
                "unit_number": self.var_unit.get().strip(),
                "session_date": str(d),
                "start_time": self.var_start.get().strip(),
                "end_time": self.var_end.get().strip(),
                "duration_min": dur,
                "base_price": base,
                "discount_percent": pct,
                "final_price": final,
                "payment_type_id": pay_id,
                "subscription_id": sub_id,
                "operator": self.var_operator.get().strip(),
                "comment": self.var_comment.get().strip(),
                "updated_at": now,
                "updated_by": self.db.current_user,
            }

            def _refund_one(sub_id_):
                s_ = self.app.subscription_service.get(sub_id_)
                if not s_:
                    return
                used = max(0, (s_["used_sessions"] or 0) - 1)
                self.app.subscription_service.update(
                    s_["id"], used_sessions=used,
                    status="активен" if used < s_["total_sessions"]
                    else "использован")

            def _use_n(sub_id_, n):
                for _ in range(n):
                    self.app.subscription_service.use_session(sub_id_)

            if self.session_id:
                old = self.db.query_one("SELECT * FROM sessions WHERE id=?",
                                        (self.session_id,))
                sets = ", ".join(f"{k}=?" for k in data)
                self.db.execute(
                    f"UPDATE sessions SET {sets} WHERE id=?",
                    list(data.values()) + [self.session_id],
                )
                self.db.log_change("sessions", self.session_id, "update",
                                   dict(old) if old else None, data)

                old_sub = old["subscription_id"] if old else None
                if old_sub and old_sub != sub_id:
                    _refund_one(old_sub)
                    if sub_id:
                        _use_n(sub_id, sub_count)
                elif sub_id:
                    delta = sub_count - 1
                    if delta > 0:
                        _use_n(sub_id, delta)
                    elif delta < 0:
                        for _ in range(-delta):
                            _refund_one(sub_id)
                messagebox.showinfo("Готово", "Визит обновлён", parent=self)
            else:
                data["created_at"] = now
                cols = ",".join(data.keys())
                ph = ",".join("?" * len(data))
                cur = self.db.execute(
                    f"INSERT INTO sessions ({cols}) VALUES ({ph})",
                    list(data.values()))
                sid = cur.lastrowid
                self.db.log_change("sessions", sid, "create", None, data)
                if sub_id:
                    _use_n(sub_id, sub_count)
                messagebox.showinfo("Готово", "Визит сохранён", parent=self)

            if self.on_saved:
                self.on_saved()
            if not self.session_id and not self.is_dialog:
                try:
                    if self.winfo_exists():
                        self.clear()
                except Exception:
                    pass
            self.app.refresh_all()

        except Exception as e:
            logger.exception("Ошибка сохранения визита")
            messagebox.showerror("Ошибка",
                                 f"Не удалось сохранить визит:\n{e}",
                                 parent=self)


# ===========================================================================
# ВКЛАДКА 1. НОВЫЙ ВИЗИТ
# ===========================================================================
class NewVisitTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.form = VisitForm(self, app, on_saved=self.reload)
        self.form.pack(fill="both", expand=True)

    def reload(self):
        self.form.reload_directories()


# ===========================================================================
# ВКЛАДКА 2. АБОНЕМЕНТЫ
# ===========================================================================
class SubscriptionsTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self._build()
        self.reload()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=4)

        ttk.Label(top, text="АБОНЕМЕНТЫ", style="Header.TLabel").pack(side="left")
        ttk.Label(top, text="Фильтр по статусу:").pack(side="left", padx=(20, 4))
        self.var_status = tk.StringVar(value="все")
        cmb = ttk.Combobox(top, textvariable=self.var_status, state="readonly",
                           width=14,
                           values=["все", "активен", "истёк", "использован",
                                   "заморожен"])
        cmb.pack(side="left")
        cmb.bind("<<ComboboxSelected>>", lambda e: self.reload())

        ttk.Button(top, text="Поиск", command=self.search_by_number).pack(
            side="left", padx=8)

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Новый абонемент", style="Accent.TButton",
                   command=self.new_sub).pack(side="left", padx=2)
        ttk.Button(btns, text="Редактировать",
                   command=self.edit_sub).pack(side="left", padx=2)
        ttk.Button(btns, text="Продлить (30 дн.)",
                   command=self.extend_sub).pack(side="left", padx=2)
        ttk.Button(btns, text="Заморозить/Разморозить",
                   command=self.toggle_freeze).pack(side="left", padx=2)
        ttk.Button(btns, text="Удалить", style="Danger.TButton",
                   command=self.delete_sub).pack(side="left", padx=2)
        ttk.Button(btns, text="Экспорт PDF",
                   command=self.export_pdf).pack(side="right", padx=2)

        cols = ("id", "number", "client", "type", "sessions", "used", "price",
                "purchase", "until", "status", "payment")
        heads = ("ID", "Номер", "Клиент", "Тип", "Сеансов", "Исп.", "Цена",
                 "Куплен", "Действует до", "Статус", "Оплата")
        widths = (40, 130, 200, 170, 70, 60, 80, 100, 120, 100, 90)
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=6)
        self.tree.bind("<Double-1>", lambda e: self.edit_sub())

    def reload(self):
        self.app.subscription_service.autoexpire()
        self.tree.delete(*self.tree.get_children())
        rows = self.app.subscription_service.list(self.var_status.get())
        for r in rows:
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["id"], r["number"], r["client_fio"] or "—", r["type"] or "",
                r["total_sessions"], r["used_sessions"],
                f"{r['price']:.2f}",
                fmt_date(r["purchase_date"]),
                fmt_date(r["valid_until"]),
                r["status"], r["payment_name"] or "",
            ))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Внимание", "Выберите абонемент")
            return None
        return int(sel[0])

    def search_by_number(self):
        import tkinter.simpledialog as sd
        num = sd.askstring("Поиск", "Введите номер абонемента:")
        if not num:
            return
        for iid in self.tree.get_children():
            if num.lower() in str(self.tree.item(iid, "values")[1]).lower():
                self.tree.selection_set(iid)
                self.tree.see(iid)
                return
        messagebox.showinfo("Поиск", "Абонемент не найден")

    def new_sub(self):
        SubscriptionDialog(self, self.app, on_saved=self.reload)

    def edit_sub(self):
        sid = self._selected()
        if sid:
            SubscriptionDialog(self, self.app, sub_id=sid, on_saved=self.reload)

    def extend_sub(self):
        sid = self._selected()
        if not sid:
            return
        try:
            self.app.subscription_service.extend(sid, 30)
            self.reload()
            messagebox.showinfo("Готово", "Абонемент продлён на 30 дней")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def toggle_freeze(self):
        sid = self._selected()
        if not sid:
            return
        sub = self.app.subscription_service.get(sid)
        if sub["status"] == "заморожен":
            self.app.subscription_service.unfreeze(sid)
        else:
            self.app.subscription_service.freeze(sid)
        self.reload()

    def delete_sub(self):
        sid = self._selected()
        if not sid:
            return
        if messagebox.askyesno("Подтверждение",
                               "Удалить абонемент? Действие можно отменить "
                               "только через БД."):
            self.app.subscription_service.soft_delete(sid)
            self.reload()

    def export_pdf(self):
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import mm
        except ImportError:
            messagebox.showwarning(
                "Нет модуля",
                "Для экспорта в PDF установите reportlab:\n\n"
                "pip install reportlab")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile="abonements.pdf")
        if not path:
            return
        try:
            c = canvas.Canvas(path, pagesize=A4)
            w, h = A4
            c.setFont("Helvetica-Bold", 14)
            c.drawString(20 * mm, h - 20 * mm, "Абонементы — Зушек Дрифт")
            c.setFont("Helvetica", 8)
            y = h - 30 * mm
            headers = ["Номер", "Клиент", "Тип", "Исп.", "Цена", "До", "Статус"]
            xs = [20, 70, 160, 250, 275, 310, 360]
            for x, hd in zip(xs, headers):
                c.drawString(x * mm / 3.5, y, hd)
            y -= 5 * mm
            for iid in self.tree.get_children():
                v = self.tree.item(iid, "values")
                row = [str(v[1]), str(v[2])[:28], str(v[3])[:24],
                       f"{v[5]}/{v[4]}", str(v[6]), str(v[8]), str(v[9])]
                for x, val in zip(xs, row):
                    c.drawString(x * mm / 3.5, y, val)
                y -= 4.5 * mm
                if y < 20 * mm:
                    c.showPage()
                    y = h - 20 * mm
            c.save()
            messagebox.showinfo("Готово", f"PDF сохранён:\n{path}")
        except Exception as e:
            logger.exception("Ошибка экспорта PDF")
            messagebox.showerror("Ошибка", f"Не удалось сохранить PDF:\n{e}")


class SubscriptionDialog(tk.Toplevel):
    """Диалог создания/редактирования абонемента."""

    def __init__(self, master, app, sub_id=None, on_saved=None):
        super().__init__(master)
        self.app = app
        self.sub_id = sub_id
        self.on_saved = on_saved
        self.title("Новый абонемент" if not sub_id
                   else "Редактирование абонемента")
        self.configure(bg=C["bg"])
        self.geometry("560x540")
        self.transient(master)
        self.grab_set()

        self.var_number = tk.StringVar()
        self.var_client = tk.StringVar(value="— не выбран —")
        self.var_type = tk.StringVar()
        self.var_sessions = tk.StringVar(value="10")
        self.var_price = tk.StringVar(value="0")
        self.var_purchase = tk.StringVar(value=fmt_date(date.today()))
        self.var_until = tk.StringVar(
            value=fmt_date(date.today() + timedelta(days=30)))
        self.var_status = tk.StringVar(value="активен")
        self.var_payment = tk.StringVar()
        self.var_used = tk.StringVar(value="0")
        self.client_id = None

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(frm, text="Номер:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_number, width=30).grid(
            row=r, column=1, sticky="ew", padx=6)
        ttk.Button(frm, text="Сгенерировать",
                   command=lambda: self.var_number.set(
                       app.subscription_service.generate_number())).grid(
            row=r, column=2, padx=4)
        r += 1

        ttk.Label(frm, text="Клиент:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Label(frm, textvariable=self.var_client).grid(
            row=r, column=1, sticky="w", padx=6)
        ttk.Button(frm, text="Выбрать", command=self.pick_client).grid(
            row=r, column=2, padx=4)
        r += 1

        ttk.Label(frm, text="Тип абонемента:").grid(row=r, column=0, sticky="w",
                                                    pady=4)
        self.cmb_type = ttk.Combobox(frm, textvariable=self.var_type,
                                     state="readonly", width=30)
        self.cmb_type.grid(row=r, column=1, sticky="ew", padx=6)
        self.cmb_type.bind("<<ComboboxSelected>>", lambda e: self._apply_type())
        r += 1

        ttk.Label(frm, text="Кол-во сеансов:").grid(row=r, column=0, sticky="w",
                                                    pady=4)
        ttk.Entry(frm, textvariable=self.var_sessions, width=30).grid(
            row=r, column=1, sticky="ew", padx=6)
        r += 1

        ttk.Label(frm, text="Цена:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_price, width=30).grid(
            row=r, column=1, sticky="ew", padx=6)
        r += 1

        ttk.Label(frm, text="Дата покупки (ДД-ММ-ГГГГ):").grid(
            row=r, column=0, sticky="w", pady=4)
        e_p = ttk.Entry(frm, textvariable=self.var_purchase, width=30)
        e_p.grid(row=r, column=1, sticky="ew", padx=6)
        e_p.bind("<KeyRelease>", self._on_purchase_key)
        self._e_purchase = e_p
        r += 1

        ttk.Label(frm, text="Действует до (ДД-ММ-ГГГГ):").grid(
            row=r, column=0, sticky="w", pady=4)
        e_u = ttk.Entry(frm, textvariable=self.var_until, width=30)
        e_u.grid(row=r, column=1, sticky="ew", padx=6)
        e_u.bind("<KeyRelease>", self._on_until_key)
        self._e_until = e_u
        r += 1

        ttk.Label(frm, text="Использовано сеансов:").grid(row=r, column=0,
                                                          sticky="w", pady=4)
        ttk.Entry(frm, textvariable=self.var_used, width=30).grid(
            row=r, column=1, sticky="ew", padx=6)
        r += 1

        ttk.Label(frm, text="Статус:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Combobox(frm, textvariable=self.var_status, state="readonly",
                     values=["активен", "истёк", "использован", "заморожен"],
                     width=28).grid(row=r, column=1, sticky="ew", padx=6)
        r += 1

        ttk.Label(frm, text="Формат оплаты:").grid(row=r, column=0, sticky="w",
                                                   pady=4)
        self.cmb_pay = ttk.Combobox(frm, textvariable=self.var_payment,
                                    state="readonly", width=28)
        self.cmb_pay.grid(row=r, column=1, sticky="ew", padx=6)
        r += 1

        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=3, pady=16, sticky="e")
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(
            side="right", padx=4)
        ttk.Button(btns, text="Сохранить", style="Accent.TButton",
                   command=self.save).pack(side="right")

        self.types = app.directory_service.list("subscription_types",
                                                only_active=True)
        self._type_map = {t["name"]: t for t in self.types}
        self.cmb_type["values"] = list(self._type_map.keys())

        self.payments = app.directory_service.payment_types_active()
        self._pay_map = {p["name"]: p["id"] for p in self.payments}
        self.cmb_pay["values"] = list(self._pay_map.keys())

        if sub_id:
            self._load(sub_id)
        else:
            self.var_number.set(app.subscription_service.generate_number())
            if self.cmb_type["values"]:
                self.cmb_type.current(0)
                self._apply_type()
            if self.cmb_pay["values"]:
                self.cmb_pay.current(0)

    def _on_purchase_key(self, event):
        raw = self.var_purchase.get()
        formatted = format_date_input(raw)
        if formatted != raw:
            self.var_purchase.set(formatted)
            self._e_purchase.icursor("end")

    def _on_until_key(self, event):
        raw = self.var_until.get()
        formatted = format_date_input(raw)
        if formatted != raw:
            self.var_until.set(formatted)
            self._e_until.icursor("end")

    def _apply_type(self):
        t = self._type_map.get(self.var_type.get())
        if t:
            self.var_sessions.set(str(t["total_sessions"]))
            self.var_price.set(f"{t['default_price']:.2f}")
            self.var_until.set(fmt_date(
                date.today() + timedelta(days=t["validity_days"] or 30)))

    def pick_client(self):
        dlg = ClientPickerDialog(self, self.app, on_select=self.set_client)
        self.wait_window(dlg)

    def set_client(self, client_id):
        c = self.app.client_service.get(client_id)
        if c:
            self.client_id = client_id
            self.var_client.set(f"{c['fio']} | {c['phone'] or '—'}")

    def _load(self, sub_id):
        s = self.app.subscription_service.get(sub_id)
        if not s:
            return
        self.var_number.set(s["number"])
        self.set_client(s["client_id"])
        self.var_type.set(s["type"] or "")
        self.var_sessions.set(str(s["total_sessions"]))
        self.var_used.set(str(s["used_sessions"]))
        self.var_price.set(f"{s['price']:.2f}")
        self.var_purchase.set(fmt_date(s["purchase_date"]))
        self.var_until.set(fmt_date(s["valid_until"]))
        self.var_status.set(s["status"] or "активен")
        pt = self.app.db.query_one("SELECT name FROM payment_types WHERE id=?",
                                   (s["payment_type_id"],))
        if pt:
            self.var_payment.set(pt["name"])

    def save(self):
        if not self.client_id:
            messagebox.showwarning("Внимание", "Выберите клиента", parent=self)
            return
        if not self.var_number.get().strip():
            messagebox.showwarning("Внимание", "Укажите номер абонемента",
                                   parent=self)
            return
        d1 = parse_date(self.var_purchase.get())
        d2 = parse_date(self.var_until.get())
        if not d1 or not d2:
            messagebox.showwarning("Внимание",
                                   "Проверьте даты (ДД-ММ-ГГГГ)", parent=self)
            return
        try:
            total = int(self.var_sessions.get())
            used = int(self.var_used.get())
            price = float(self.var_price.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Внимание", "Проверьте числовые поля",
                                   parent=self)
            return
        pay_id = self._pay_map.get(self.var_payment.get())
        try:
            if self.sub_id:
                self.app.subscription_service.update(
                    self.sub_id, number=self.var_number.get().strip(),
                    client_id=self.client_id, type=self.var_type.get(),
                    total_sessions=total, used_sessions=used, price=price,
                    purchase_date=str(d1), valid_until=str(d2),
                    status=self.var_status.get(), payment_type_id=pay_id)
                self.app.db.log_change("subscriptions", self.sub_id, "update")
            else:
                self.app.subscription_service.create(
                    self.client_id, self.var_type.get(), total, price,
                    d1, d2, pay_id, number=self.var_number.get().strip())
        except Exception as e:
            messagebox.showerror("Ошибка", str(e), parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.app.refresh_all()
        self.destroy()


# ===========================================================================
# ВКЛАДКА 3. ПОИСК КЛИЕНТА
# ===========================================================================
class ClientSearchTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.current_client = None
        self._build()
        self.reload()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=4)
        ttk.Label(top, text="ПОИСК КЛИЕНТА", style="Header.TLabel").pack(
            side="left")

        self.var_q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.var_q, width=40)
        e.pack(side="left", padx=10)
        e.bind("<Return>", lambda ev: self.reload())
        ttk.Button(top, text="Найти", style="Accent.TButton",
                   command=self.reload).pack(side="left")
        ttk.Button(top, text="Новый клиент", command=self.new_client).pack(
            side="left", padx=6)

        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, pady=6)

        left = ttk.Frame(pane)
        cols = ("id", "fio", "phone", "birth", "age", "visits", "total")
        heads = ("ID", "ФИО", "Телефон", "Дата рожд.", "Возраст",
                 "Визитов", "Сумма")
        widths = (40, 200, 140, 110, 90, 70, 90)
        self.tree = ttk.Treeview(left, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_card())
        self.tree.bind("<Double-1>", lambda e: self.edit_client())
        pane.add(left, weight=1)

        right = ttk.Frame(pane, padding=10)
        pane.add(right, weight=1)

        ttk.Label(right, text="Карточка клиента", style="Header.TLabel").pack(
            anchor="w")
        self.lbl_card = ttk.Label(right, text="— выберите клиента —",
                                  style="Big.TLabel", wraplength=420,
                                  justify="left")
        self.lbl_card.pack(anchor="w", pady=8)

        self.lbl_stats = ttk.Label(right, text="", justify="left")
        self.lbl_stats.pack(anchor="w", pady=4)

        btns = ttk.Frame(right)
        btns.pack(anchor="w", pady=8)
        ttk.Button(btns, text="Редактировать", command=self.edit_client).pack(
            side="left", padx=2)
        ttk.Button(btns, text="Архивировать",
                   command=self.archive_client).pack(side="left", padx=2)
        ttk.Button(btns, text="Удалить", style="Danger.TButton",
                   command=self.delete_client).pack(side="left", padx=2)

        ttk.Label(right, text="История визитов:", style="Sub.TLabel").pack(
            anchor="w", pady=(10, 2))
        cols2 = ("date", "time", "att", "unit", "price", "pay")
        heads2 = ("Дата", "Время", "Услуга", "Номер", "Сумма", "Оплата")
        widths2 = (100, 60, 150, 70, 80, 90)
        self.tree_hist = ttk.Treeview(right, columns=cols2, show="headings",
                                      height=7)
        for c, h, w in zip(cols2, heads2, widths2):
            self.tree_hist.heading(c, text=h)
            self.tree_hist.column(c, width=w, anchor="w")
        self.tree_hist.pack(fill="both", expand=True)
        self.tree_hist.bind("<Double-1>", lambda e: self.edit_visit())

        ttk.Label(right, text="Абонементы клиента:",
                  style="Sub.TLabel").pack(anchor="w", pady=(10, 2))
        cols3 = ("number", "type", "left", "until", "status")
        heads3 = ("Номер", "Тип", "Остаток", "До", "Статус")
        widths3 = (120, 140, 80, 90, 100)
        self.tree_subs = ttk.Treeview(right, columns=cols3, show="headings",
                                      height=5)
        for c, h, w in zip(cols3, heads3, widths3):
            self.tree_subs.heading(c, text=h)
            self.tree_subs.column(c, width=w, anchor="w")
        self.tree_subs.pack(fill="both", expand=True)
        self.tree_subs.bind("<Double-1>", lambda e: self.edit_sub())

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.app.client_service.search(self.var_q.get())
        for r in rows:
            st = self.app.client_service.stats(r["id"])
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["id"], r["fio"], r["phone"] or "",
                fmt_date(r["birth_date"]),
                _human_age(r["birth_date"]),
                st["visits"], f"{st['total']:.2f}"))

    def _selected_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def show_card(self):
        cid = self._selected_id()
        if not cid:
            return
        c = self.app.client_service.get(cid)
        if not c:
            return
        self.current_client = cid
        age_str = _human_age(c["birth_date"])
        age_part = f", {age_str}" if age_str else ""
        last_visit = _human_last_visit(self.app.db, cid)

        self.lbl_card.configure(
            text=f"{c['fio']}\n{c['phone'] or '—'}\n"
                 f"ДР: {fmt_date(c['birth_date']) or '—'}{age_part}")
        st = self.app.client_service.stats(cid)
        self.lbl_stats.configure(
            text=f"Последний визит: {last_visit}\n"
                 f"Визитов: {st['visits']}     "
                 f"Сумма: {st['total']:.2f} руб.\n"
                 f"Заметки: {c['notes'] or '—'}")

        self.tree_hist.delete(*self.tree_hist.get_children())
        for s in self.app.client_service.history(cid):
            self.tree_hist.insert("", "end", iid=str(s["id"]), values=(
                fmt_date(s["session_date"]), s["start_time"] or "",
                s["attraction_name"] or "—", s["unit_number"] or "",
                f"{s['final_price']:.2f}", s["payment_name"] or ""))

        # Авто-просрочка: помечаем истёкшие абонементы актуальным статусом
        try:
            self.app.subscription_service.autoexpire()
        except Exception:
            pass

        self.tree_subs.delete(*self.tree_subs.get_children())
        subs = self.app.subscription_service.all_for_client(cid)
        if not subs:
            self.tree_subs.insert("", "end", iid="__empty__",
                                  values=("— нет абонементов —",
                                          "", "", "", ""))
        else:
            for s in subs:
                remaining = (s["total_sessions"] or 0) - (
                    s["used_sessions"] or 0)
                self.tree_subs.insert("", "end", iid=str(s["id"]), values=(
                    s["number"],
                    s["type"] or "",
                    f"{remaining}/{s['total_sessions']}",
                    fmt_date(s["valid_until"]),
                    s["status"] or ""))

    def new_client(self):
        dlg = ClientDialog(self, self.app)
        self.wait_window(dlg)
        self.reload()

    def edit_client(self):
        cid = self._selected_id()
        if not cid:
            return
        dlg = ClientDialog(self, self.app, client_id=cid)
        self.wait_window(dlg)
        self.reload()
        self.show_card()
        self.app.refresh_all()

    def archive_client(self):
        cid = self._selected_id()
        if not cid:
            return
        if messagebox.askyesno("Подтверждение", "Архивировать клиента?"):
            self.app.client_service.archive(cid)
            self.reload()

    def delete_client(self):
        cid = self._selected_id()
        if not cid:
            return
        if messagebox.askyesno(
                "Подтверждение",
                "Удалить клиента? Будет выполнено мягкое удаление "
                "(можно восстановить из БД)."):
            self.app.client_service.soft_delete(cid)
            self.reload()
            self.lbl_card.configure(text="— выберите клиента —")

    def edit_visit(self):
        sel = self.tree_hist.selection()
        if not sel:
            return
        dlg = VisitEditDialog(self, self.app, int(sel[0]),
                              on_saved=self.show_card)
        self.wait_window(dlg)

    def edit_sub(self):
        """Открыть выбранный абонемент клиента в диалоге редактирования."""
        sel = self.tree_subs.selection()
        if not sel or sel[0] == "__empty__":
            return
        try:
            sid = int(sel[0])
        except ValueError:
            return
        dlg = SubscriptionDialog(self, self.app, sub_id=sid,
                                 on_saved=self.show_card)
        self.wait_window(dlg)


# ===========================================================================
# ВКЛАДКА 4. УЧЁТ ДНЯ
# ===========================================================================
class DayAccountingTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self._build()
        self.reload()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=4)
        ttk.Label(top, text="УЧЁТ ДНЯ", style="Header.TLabel").pack(side="left")

        self.var_date = tk.StringVar(value=fmt_date(date.today()))
        ttk.Label(top, text="Дата (ДД-ММ-ГГГГ):").pack(side="left",
                                                       padx=(20, 4))
        self.e_date = ttk.Entry(top, textvariable=self.var_date, width=14)
        self.e_date.pack(side="left")
        self.e_date.bind("<KeyRelease>", self._on_date_key)
        self.e_date.bind("<Return>", lambda ev: self.reload())
        ttk.Button(top, text="Обновить", command=self.reload).pack(
            side="left", padx=6)
        ttk.Button(top, text="◀ День", command=lambda: self.shift(-1)).pack(
            side="left", padx=2)
        ttk.Button(top, text="День ▶", command=lambda: self.shift(1)).pack(
            side="left", padx=2)
        ttk.Button(top, text="Сегодня", command=self.today).pack(
            side="left", padx=2)

        self.card = ttk.Frame(self, style="Card.TFrame", padding=10)
        self.card.pack(fill="x", pady=8)
        self.lbl_clients = ttk.Label(self.card, text="Клиентов: 0",
                                     style="Big.TLabel")
        self.lbl_clients.pack(side="left", padx=10)
        self.lbl_sessions = ttk.Label(self.card, text="Сеансов: 0",
                                      style="Big.TLabel")
        self.lbl_sessions.pack(side="left", padx=10)
        self.lbl_revenue = ttk.Label(self.card, text="Выручка: 0.00",
                                     style="Big.TLabel")
        self.lbl_revenue.pack(side="left", padx=10)

        self.lbl_pay = ttk.Label(self.card, text="", justify="left",
                                 style="Card.TLabel")
        self.lbl_pay.pack(side="left", padx=20)

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="Редактировать визит",
                   command=self.edit_visit).pack(side="left", padx=2)
        ttk.Button(btns, text="Удалить визит", style="Danger.TButton",
                   command=self.delete_visit).pack(side="left", padx=2)
        ttk.Button(btns, text="Добавить задним числом",
                   style="Warn.TButton", command=self.add_backdated).pack(
            side="left", padx=2)

        cols = ("id", "client", "att", "unit", "start", "end", "dur",
                "base", "disc", "final", "pay", "op", "comment")
        heads = ("ID", "Клиент", "Услуга", "Номер", "Начало", "Конец",
                 "Мин", "База", "Скидка %", "Итого", "Оплата", "Оператор",
                 "Комментарий")
        widths = (40, 170, 130, 70, 60, 60, 50, 70, 70, 80, 90, 90, 150)
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=6)
        self.tree.bind("<Double-1>", lambda e: self.edit_visit())

        ttk.Label(self, text="Разбивка по услугам:",
                  style="Sub.TLabel").pack(anchor="w", pady=(6, 2))
        cols2 = ("name", "cnt", "total")
        self.tree_att = ttk.Treeview(self, columns=cols2, show="headings",
                                     height=5)
        for c, h, w in zip(cols2, ("Услуга / товар", "Сеансов", "Выручка"),
                           (250, 100, 120)):
            self.tree_att.heading(c, text=h)
            self.tree_att.column(c, width=w, anchor="w")
        self.tree_att.pack(fill="x")

    def _on_date_key(self, event):
        raw = self.var_date.get()
        formatted = format_date_input(raw)
        if formatted != raw:
            self.var_date.set(formatted)
            self.e_date.icursor("end")

    def shift(self, days):
        d = parse_date(self.var_date.get()) or date.today()
        self.var_date.set(fmt_date(d + timedelta(days=days)))
        self.reload()

    def today(self):
        self.var_date.set(fmt_date(date.today()))
        self.reload()

    def reload(self):
        d = parse_date(self.var_date.get())
        if not d:
            return
        self.var_date.set(fmt_date(d))
        s = self.app.report_service.day_summary(d)
        self.lbl_clients.configure(text=f"Клиентов: {s['clients']}")
        self.lbl_sessions.configure(text=f"Сеансов: {s['sessions']}")
        self.lbl_revenue.configure(text=f"Выручка: {s['revenue']:.2f} руб.")
        pay_txt = "  |  ".join(
            f"{r['name'] or '—'}: {r['total']:.0f}" for r in s["by_pay"]
        ) or "нет данных"
        self.lbl_pay.configure(text="Оплаты: " + pay_txt)

        self.tree.delete(*self.tree.get_children())
        for r in self.app.report_service.day_sessions(d):
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["id"], r["client_fio"] or "—", r["attraction_name"] or "—",
                r["unit_number"] or "", r["start_time"] or "",
                r["end_time"] or "", r["duration_min"] or 0,
                f"{r['base_price']:.2f}", f"{r['discount_percent']:.0f}",
                f"{r['final_price']:.2f}", r["payment_name"] or "",
                r["operator"] or "", r["comment"] or ""))

        self.tree_att.delete(*self.tree_att.get_children())
        for r in s["by_att"]:
            self.tree_att.insert("", "end", values=(
                r["name"] or "—", r["cnt"], f"{r['total']:.2f}"))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Внимание", "Выберите визит")
            return None
        return int(sel[0])

    def edit_visit(self):
        sid = self._selected()
        if sid:
            dlg = VisitEditDialog(self, self.app, sid, on_saved=self.reload)
            self.wait_window(dlg)

    def delete_visit(self):
        sid = self._selected()
        if not sid:
            return
        if messagebox.askyesno("Подтверждение",
                               "Удалить визит? (мягкое удаление)"):
            self.app.db.execute(
                "UPDATE sessions SET is_deleted=1, updated_at=?, updated_by=? "
                "WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"),
                 self.app.db.current_user, sid))
            self.app.db.log_change("sessions", sid, "delete")
            self.reload()

    def add_backdated(self):
        dlg = VisitEditDialog(self, self.app, None, on_saved=self.reload)
        self.wait_window(dlg)


# ===========================================================================
# ВКЛАДКА 5. ОТЧЁТЫ
# ===========================================================================
class ReportsTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self._last_data = []
        self._title = ""
        self._build()
        self.reload()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=4)
        ttk.Label(top, text="ОТЧЁТЫ", style="Header.TLabel").pack(side="left")

        today = date.today()
        self.var_from = tk.StringVar(value=fmt_date(today.replace(day=1)))
        self.var_to = tk.StringVar(value=fmt_date(today))
        ttk.Label(top, text="с (ДД-ММ-ГГГГ):").pack(side="left", padx=(20, 2))
        self.e_from = ttk.Entry(top, textvariable=self.var_from, width=12)
        self.e_from.pack(side="left")
        self.e_from.bind("<KeyRelease>",
                         lambda e: self._on_dkey(self.var_from, self.e_from))
        ttk.Label(top, text="по:").pack(side="left", padx=2)
        self.e_to = ttk.Entry(top, textvariable=self.var_to, width=12)
        self.e_to.pack(side="left")
        self.e_to.bind("<KeyRelease>",
                       lambda e: self._on_dkey(self.var_to, self.e_to))

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=6)
        reports = [
            ("1. Дни недели", self.rep_weekday),
            ("2. Популярность услуг", self.rep_attractions),
            ("3. Топ-10 клиентов", self.rep_top),
            ("4. Выручка по дням", lambda: self.rep_revenue("день")),
            ("4б. По неделям", lambda: self.rep_revenue("неделя")),
            ("4в. По месяцам", lambda: self.rep_revenue("месяц")),
            ("5. Формы оплаты", self.rep_payments),
            ("6. Доля скидок", self.rep_discounts),
            ("7. Абонементы", self.rep_subs),
        ]
        for text, cmd in reports:
            ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=2)

        exp = ttk.Frame(self)
        exp.pack(fill="x", pady=2)
        ttk.Button(exp, text="График", style="Warn.TButton",
                   command=self.show_chart).pack(side="left", padx=2)
        ttk.Button(exp, text="Excel", style="Accent.TButton",
                   command=self.export_excel).pack(side="left", padx=2)
        ttk.Button(exp, text="PDF", command=self.export_pdf).pack(
            side="left", padx=2)

        self.tree = ttk.Treeview(self, show="headings")
        self.tree.pack(fill="both", expand=True, pady=6)

    def _on_dkey(self, var, entry):
        raw = var.get()
        formatted = format_date_input(raw)
        if formatted != raw:
            var.set(formatted)
            entry.icursor("end")

    def _range(self):
        d1 = parse_date(self.var_from.get())
        d2 = parse_date(self.var_to.get())
        if not d1 or not d2:
            messagebox.showwarning("Внимание",
                                   "Проверьте даты (ДД-ММ-ГГГГ)")
            return None
        if d1 > d2:
            d1, d2 = d2, d1
        return d1, d2

    def _show(self, title, rows):
        self.tree.delete(*self.tree.get_children())
        self._last_data = rows
        self._title = title
        if not rows:
            self.tree["columns"] = ("empty",)
            self.tree.heading("empty", text="Нет данных за выбранный период")
            self.tree.column("empty", width=400)
            return
        cols = list(rows[0].keys())
        self.tree["columns"] = cols
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=max(90, len(c) * 11), anchor="w")
        for r in rows:
            self.tree.insert("", "end", values=[r[c] for c in cols])

    def rep_weekday(self):
        r = self._range()
        if r:
            self._show("Среднее клиентов по дням недели",
                       self.app.report_service.report_weekday(*r))

    def rep_attractions(self):
        r = self._range()
        if r:
            self._show("Популярность услуг",
                       self.app.report_service.report_attractions(*r))

    def rep_top(self):
        r = self._range()
        if r:
            self._show("Топ-10 клиентов",
                       self.app.report_service.report_top_clients(*r))

    def rep_revenue(self, period):
        r = self._range()
        if r:
            self._show(f"Выручка по {period}ам",
                       self.app.report_service.report_revenue(*r, period=period))

    def rep_payments(self):
        r = self._range()
        if r:
            self._show("Распределение по формам оплаты",
                       self.app.report_service.report_payments(*r))

    def rep_discounts(self):
        r = self._range()
        if r:
            self._show("Доля сеансов со скидкой",
                       self.app.report_service.report_discounts(*r))

    def rep_subs(self):
        r = self._range()
        if r:
            self._show("Активность абонементов",
                       self.app.report_service.report_subscriptions(*r))

    def reload(self):
        self.rep_attractions()

    def show_chart(self):
        if not self._last_data:
            messagebox.showinfo("График", "Сначала постройте отчёт")
            return
        try:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except ImportError:
            messagebox.showwarning(
                "Нет модуля",
                "Для графиков установите matplotlib:\n\n"
                "pip install matplotlib")
            return

        cols = list(self._last_data[0].keys())
        label_col = cols[0]
        value_col = None
        for c in cols[1:]:
            if isinstance(self._last_data[0][c], (int, float)):
                value_col = c
                break
        if not value_col:
            messagebox.showinfo("График", "Нет числовых данных для графика")
            return

        win = tk.Toplevel(self)
        win.title("График")
        win.geometry("900x560")
        labels = [str(r[label_col])[:14] for r in self._last_data]
        values = [r[value_col] for r in self._last_data]

        fig = Figure(figsize=(9, 5), dpi=100)
        ax = fig.add_subplot(111)
        ax.bar(range(len(labels)), values,
               color=["#4D96FF", "#FF8C42", "#FFD93D", "#4CAF50", "#E74C3C"]
               * (len(labels) // 5 + 1))
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel(value_col)
        ax.set_title(self._title or "Отчёт")
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def export_excel(self):
        if not self._last_data:
            messagebox.showinfo("Excel", "Сначала постройте отчёт")
            return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
        except ImportError:
            messagebox.showwarning(
                "Нет модуля",
                "Для экспорта в Excel установите openpyxl:\n\n"
                "pip install openpyxl")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
            initialfile="report.xlsx")
        if not path:
            return
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Отчёт"
            title = self._title or "Отчёт"
            ws.append([title])
            ws["A1"].font = Font(bold=True, size=14, color="FF8C42")

            cols = list(self._last_data[0].keys())
            ws.append(cols)
            for cell in ws[2]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="4D96FF")
            for r in self._last_data:
                ws.append([r[c] for c in cols])
            for i, c in enumerate(cols, 1):
                ws.column_dimensions[chr(64 + i)].width = max(14, len(c) + 4)
            wb.save(path)
            messagebox.showinfo("Готово", f"Excel сохранён:\n{path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить Excel:\n{e}")

    def export_pdf(self):
        if not self._last_data:
            messagebox.showinfo("PDF", "Сначала постройте отчёт")
            return
        try:
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import mm
        except ImportError:
            messagebox.showwarning(
                "Нет модуля",
                "Для PDF установите reportlab:\n\npip install reportlab")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
            initialfile="report.pdf")
        if not path:
            return
        try:
            c = canvas.Canvas(path, pagesize=landscape(A4))
            w, h = landscape(A4)
            title = self._title or "Отчёт"
            c.setFont("Helvetica-Bold", 14)
            c.drawString(15 * mm, h - 15 * mm,
                         f"{title} — {self.var_from.get()} — {self.var_to.get()}")
            cols = list(self._last_data[0].keys())
            col_w = (w - 30 * mm) / len(cols)
            y = h - 25 * mm
            c.setFont("Helvetica-Bold", 9)
            for i, col in enumerate(cols):
                c.drawString(15 * mm + i * col_w, y, str(col)[:20])
            y -= 6 * mm
            c.setFont("Helvetica", 8)
            for r in self._last_data:
                if y < 15 * mm:
                    c.showPage()
                    y = h - 20 * mm
                for i, col in enumerate(cols):
                    c.drawString(15 * mm + i * col_w, y, str(r[col])[:24])
                y -= 5.5 * mm
            c.save()
            messagebox.showinfo("Готово", f"PDF сохранён:\n{path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить PDF:\n{e}")


# ===========================================================================
# ВКЛАДКА 6. НАСТРОЙКИ / СПРАВОЧНИКИ
# ===========================================================================
DIRECTORY_CONFIG = {
    "attractions": {
        "title": "Услуги и товары",
        "table": "attractions",
        "columns": [
            ("id", "ID", 50, "int"),
            ("name", "Название", 220, "str"),
            ("type", "Категория", 160, "str"),
            ("default_price", "Цена", 90, "float"),
            ("duration_min", "Длит. (мин)", 90, "int"),
            ("is_product", "Товар", 80, "bool"),
            ("is_active", "Активен", 80, "bool"),
        ],
    },
    "payment_types": {
        "title": "Форматы оплаты",
        "table": "payment_types",
        "columns": [
            ("id", "ID", 50, "int"),
            ("name", "Название", 200, "str"),
            ("sort_order", "Порядок", 90, "int"),
            ("is_active", "Активен", 80, "bool"),
        ],
    },
    "subscription_types": {
        "title": "Типы абонементов",
        "table": "subscription_types",
        "columns": [
            ("id", "ID", 50, "int"),
            ("name", "Название", 220, "str"),
            ("total_sessions", "Сеансов", 90, "int"),
            ("default_price", "Цена", 100, "float"),
            ("validity_days", "Срок (дней)", 100, "int"),
            ("is_active", "Активен", 80, "bool"),
        ],
    },
    "discounts": {
        "title": "Скидки",
        "table": "discounts",
        "columns": [
            ("id", "ID", 50, "int"),
            ("name", "Название", 220, "str"),
            ("percent", "Скидка %", 90, "float"),
            ("condition", "Условие", 240, "str"),
            ("is_active", "Активна", 80, "bool"),
        ],
    },
    "units": {
        "title": "Единицы техники",
        "table": "units",
        "columns": [
            ("id", "ID", 50, "int"),
            ("attraction_id", "Услуга", 200, "ref:attractions:name"),
            ("unit_number", "Номер", 130, "str"),
            ("status", "Статус", 130, "choice:работает,ремонт,списана"),
            ("is_active", "Активна", 80, "bool"),
        ],
    },
    "users": {
        "title": "Пользователи / операторы",
        "table": "users",
        "columns": [
            ("id", "ID", 50, "int"),
            ("login", "Логин", 180, "str"),
            ("role", "Роль", 150, "choice:администратор,оператор,менеджер"),
            ("is_active", "Активен", 80, "bool"),
        ],
    },
    "settings": {
        "title": "Глобальные настройки",
        "table": "settings",
        "pk": "key",
        "columns": [
            ("key", "Параметр", 220, "str"),
            ("value", "Значение", 300, "str"),
            ("description", "Описание", 320, "str"),
        ],
    },
}


class DirectoryTab(ttk.Frame):
    """Универсальная вкладка для работы со справочником."""

    def __init__(self, master, app, config_key):
        super().__init__(master, padding=8)
        self.app = app
        self.cfg = DIRECTORY_CONFIG[config_key]
        self.table = self.cfg["table"]
        self.pk = self.cfg.get("pk", "id")
        self.ref_cache = {}
        self._build()
        self.reload()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=4)
        ttk.Label(top, text=self.cfg["title"], style="Header.TLabel").pack(
            side="left")
        ttk.Button(top, text="Добавить", style="Accent.TButton",
                   command=self.add_item).pack(side="right", padx=2)
        ttk.Button(top, text="Редактировать",
                   command=self.edit_item).pack(side="right", padx=2)
        ttk.Button(top, text="Архивировать / Вернуть",
                   command=self.archive_item).pack(side="right", padx=2)
        ttk.Button(top, text="Удалить", style="Danger.TButton",
                   command=self.delete_item).pack(side="right", padx=2)

        cols = tuple(c[0] for c in self.cfg["columns"])
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c in self.cfg["columns"]:
            self.tree.heading(c[0], text=c[1])
            self.tree.column(c[0], width=c[2], anchor="w")
        self.tree.pack(fill="both", expand=True, pady=6)
        self.tree.bind("<Double-1>", lambda e: self.edit_item())

    def _resolve_ref(self, ref_str):
        _, tbl, col = ref_str.split(":")
        if tbl not in self.ref_cache:
            rows = self.app.directory_service.list(tbl)
            self.ref_cache[tbl] = {r[col]: r["id"] for r in rows if r[col]}
        return self.ref_cache[tbl]

    def reload(self):
        self.ref_cache.clear()
        rows = self.app.directory_service.list(self.table)
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            values = []
            for c in self.cfg["columns"]:
                val = r[c[0]] if c[0] in r.keys() else ""
                t = c[3]
                if t.startswith("bool"):
                    val = "Да" if val else "Нет"
                elif t.startswith("ref:"):
                    ref = self._resolve_ref(t)
                    name = next((k for k, v in ref.items() if v == val), "—")
                    val = name
                values.append(val)
            self.tree.insert("", "end", iid=str(r[self.pk]), values=values)

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Внимание", "Выберите строку")
            return None
        return sel[0]

    def add_item(self):
        ItemDialog(self, self.app, self.cfg, None, on_saved=self._after_change)

    def edit_item(self):
        pk_val = self._selected()
        if pk_val is None:
            return
        if self.pk == "id":
            pk_val = int(pk_val)
        ItemDialog(self, self.app, self.cfg, pk_val, on_saved=self._after_change)

    def archive_item(self):
        pk_val = self._selected()
        if pk_val is None:
            return
        if self.pk == "id":
            pk_val = int(pk_val)
        try:
            self.app.directory_service.archive(self.table, pk_val, self.pk)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
        self._after_change()

    def delete_item(self):
        pk_val = self._selected()
        if pk_val is None:
            return
        if self.pk == "id":
            pk_val = int(pk_val)
        used = self.app.directory_service.is_used(self.table, pk_val)
        if used:
            if messagebox.askyesno(
                    "Запись используется",
                    "Эта запись уже используется в истории. Удаление нарушит\n"
                    "целостность данных. Архивировать вместо удаления?"):
                self.app.directory_service.archive(self.table, pk_val, self.pk)
                self._after_change()
            return
        if messagebox.askyesno("Подтверждение",
                               "Удалить запись безвозвратно?"):
            self.app.directory_service.delete(self.table, pk_val, self.pk)
            self._after_change()

    def _after_change(self):
        self.reload()
        self.app.refresh_all()


class ItemDialog(tk.Toplevel):
    """Универсальный диалог добавления/редактирования записи справочника."""

    def __init__(self, master, app, cfg, pk_value, on_saved=None):
        super().__init__(master)
        self.app = app
        self.cfg = cfg
        self.table = cfg["table"]
        self.pk = cfg.get("pk", "id")
        self.pk_value = pk_value
        self.on_saved = on_saved
        self.title("Добавить запись" if pk_value is None else "Редактирование")
        self.configure(bg=C["bg"])
        self.transient(master)
        self.grab_set()

        self.vars = {}
        self.ref_maps = {}

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        r = 0
        for col in cfg["columns"]:
            name, label, _, ctype = col
            if name == self.pk and pk_value is not None:
                continue
            ttk.Label(frm, text=label + ":").grid(row=r, column=0, sticky="w",
                                                  pady=4)
            var = tk.StringVar()
            self.vars[name] = var

            if ctype.startswith("bool"):
                ttk.Checkbutton(frm, variable=var).grid(row=r, column=1,
                                                        sticky="w")
                var.set("1")
            elif ctype.startswith("choice:"):
                choices = ctype.split(":", 1)[1].split(",")
                cb = ttk.Combobox(frm, textvariable=var, values=choices,
                                  state="readonly", width=30)
                cb.grid(row=r, column=1, sticky="ew", padx=6)
                if choices:
                    var.set(choices[0])
            elif ctype.startswith("ref:"):
                _, tbl, dcol = ctype.split(":")
                rows = app.directory_service.list(tbl)
                mapping = {row[dcol]: row["id"] for row in rows if row[dcol]}
                self.ref_maps[name] = mapping
                cb = ttk.Combobox(frm, textvariable=var,
                                  values=list(mapping.keys()),
                                  state="readonly", width=30)
                cb.grid(row=r, column=1, sticky="ew", padx=6)
            else:
                ttk.Entry(frm, textvariable=var, width=32).grid(
                    row=r, column=1, sticky="ew", padx=6)
            r += 1

        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=2, pady=14, sticky="e")
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(
            side="right", padx=4)
        ttk.Button(btns, text="Сохранить", style="Accent.TButton",
                   command=self.save).pack(side="right")

        if pk_value is not None:
            self._load()

    def _load(self):
        row = self.app.directory_service.get(self.table, self.pk_value, self.pk)
        if not row:
            return
        for col in self.cfg["columns"]:
            name, _, _, ctype = col
            if name not in self.vars:
                continue
            val = row[name] if name in row.keys() else ""
            if ctype.startswith("bool"):
                self.vars[name].set("1" if val else "")
            elif ctype.startswith("ref:"):
                mapping = self.ref_maps.get(name, {})
                display = next((k for k, v in mapping.items() if v == val), "")
                self.vars[name].set(display)
            else:
                self.vars[name].set("" if val is None else str(val))

    def save(self):
        data = {}
        for col in self.cfg["columns"]:
            name, _, _, ctype = col
            if name not in self.vars:
                continue
            v = self.vars[name].get()
            if ctype.startswith("bool"):
                data[name] = 1 if v else 0
            elif ctype == "int":
                try:
                    data[name] = int(v or 0)
                except ValueError:
                    messagebox.showwarning("Внимание",
                                           f"«{name}»: нужно целое число",
                                           parent=self)
                    return
            elif ctype == "float":
                try:
                    data[name] = float((v or "0").replace(",", "."))
                except ValueError:
                    messagebox.showwarning("Внимание",
                                           f"«{name}»: нужно число",
                                           parent=self)
                    return
            elif ctype.startswith("ref:"):
                mapping = self.ref_maps.get(name, {})
                data[name] = mapping.get(v)
            else:
                data[name] = v
        try:
            if self.pk_value is None:
                self.app.directory_service.create(self.table, data)
            else:
                self.app.directory_service.update(
                    self.table, self.pk_value, data, self.pk)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}",
                                 parent=self)
            return
        if self.on_saved:
            self.on_saved()
        self.app.refresh_all()
        self.destroy()


class AuditLogTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        top = ttk.Frame(self)
        top.pack(fill="x", pady=4)
        ttk.Label(top, text="Журнал изменений", style="Header.TLabel").pack(
            side="left")
        ttk.Button(top, text="Обновить", command=self.reload).pack(
            side="right")
        cols = ("id", "table", "record", "action", "user", "when")
        heads = ("ID", "Таблица", "Запись", "Действие", "Пользователь", "Когда")
        widths = (50, 150, 90, 110, 150, 180)
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=6)
        self.reload()

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.app.db.query(
            "SELECT * FROM change_log ORDER BY id DESC LIMIT 500")
        for r in rows:
            self.tree.insert("", "end", values=(
                r["id"], r["table_name"], r["record_id"], r["action"],
                r["username"], r["created_at"]))


class SettingsTab(ttk.Frame):
    """Объединяет все справочники + журнал изменений."""

    def __init__(self, master, app):
        super().__init__(master, padding=4)
        self.app = app

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self.tabs = {}
        order = ["attractions", "payment_types", "subscription_types",
                 "discounts", "units", "users", "settings"]
        for key in order:
            cfg = DIRECTORY_CONFIG[key]
            t = DirectoryTab(nb, app, key)
            nb.add(t, text=cfg["title"])
            self.tabs[key] = t

        audit = AuditLogTab(nb, app)
        nb.add(audit, text="Журнал изменений")
        self.tabs["audit"] = audit

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=4)
        ttk.Button(bottom, text="Сделать бэкап БД", style="Warn.TButton",
                   command=self.make_backup).pack(side="left", padx=4)
        ttk.Button(bottom, text="Открыть папку с БД",
                   command=self.open_folder).pack(side="left", padx=4)

    def make_backup(self):
        try:
            path = self.app.db.backup()
            messagebox.showinfo("Готово", f"Резервная копия создана:\n{path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать бэкап:\n{e}")

    def open_folder(self):
        try:
            os.startfile(BASE_DIR)
        except AttributeError:
            subprocess.Popen(["xdg-open", BASE_DIR])

    def reload(self):
        for key, tab in self.tabs.items():
            try:
                tab.reload()
            except Exception:
                pass


# ===========================================================================
# ГЛАВНОЕ ОКНО
# ===========================================================================
class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Зушек Дрифт")
        self.geometry("1280x820")
        self.minsize(1024, 700)
        self.configure(bg=C["bg"])
        apply_style(self)
        self._logo_img = None

        try:
            self.db = DatabaseManager()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть БД:\n{e}")
            self.destroy()
            return
        self.client_service = ClientService(self.db)
        self.subscription_service = SubscriptionService(self.db)
        self.directory_service = DirectoryService(self.db)
        self.report_service = ReportService(self.db)

        self._build_menu()
        self._build_ui()
        self._bind_hotkeys()
        logger.info("Приложение запущено")

    # ------------------------------------------------------------------
    def _build_menu(self):
        m = tk.Menu(self)

        file_m = tk.Menu(m, tearoff=0)
        file_m.add_command(label="Новый визит\tCtrl+N",
                           command=lambda: self._focus_tab(0))
        file_m.add_command(label="Поиск клиента\tCtrl+F",
                           command=lambda: self._focus_tab(2))
        file_m.add_separator()
        file_m.add_command(label="Сделать бэкап БД", command=self._backup)
        file_m.add_command(label="Обновить логотип", command=self._reload_logo)
        file_m.add_separator()
        file_m.add_command(label="Выход", command=self._on_close)
        m.add_cascade(label="Файл", menu=file_m)

        view_m = tk.Menu(m, tearoff=0)
        view_m.add_command(label="Обновить (F5)", command=self.refresh_all)
        m.add_cascade(label="Вид", menu=view_m)

        help_m = tk.Menu(m, tearoff=0)
        help_m.add_command(label="Проверить обновления",
                           command=lambda: check_for_updates(
                               parent=self, silent_if_no_update=False))
        help_m.add_separator()
        help_m.add_command(label="О программе", command=self._about)
        m.add_cascade(label="Справка", menu=help_m)

        self.config(menu=m)

    def _build_ui(self):
        header = tk.Frame(self, bg=C["yellow"], height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        self._header = header

        self._logo_frame = tk.Frame(header, bg=C["yellow"])
        self._logo_frame.pack(side="left", padx=(10, 6), pady=6)
        self._load_logo_into(self._logo_frame)

        self.lbl_company = tk.Label(header, text="Зушек Дрифт",
                                    bg=C["yellow"], fg=C["text"],
                                    font=("Segoe UI", 16, "bold"))
        self.lbl_company.pack(side="left", padx=6, pady=6)

        tk.Label(header, text=f"Оператор: {self.db.current_user}   ",
                 bg=C["yellow"], fg=C["text"],
                 font=("Segoe UI", 10)).pack(side="right", pady=6)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)

        self.tab_visit = NewVisitTab(self.nb, self)
        self.tab_subs = SubscriptionsTab(self.nb, self)
        self.tab_clients = ClientSearchTab(self.nb, self)
        self.tab_day = DayAccountingTab(self.nb, self)
        self.tab_reports = ReportsTab(self.nb, self)
        self.tab_settings = SettingsTab(self.nb, self)

        self.nb.add(self.tab_visit, text="  Новый визит  ")
        self.nb.add(self.tab_subs, text="  Абонементы  ")
        self.nb.add(self.tab_clients, text="  Поиск клиента  ")
        self.nb.add(self.tab_day, text="  Учёт дня  ")
        self.nb.add(self.tab_reports, text="  Отчёты  ")
        self.nb.add(self.tab_settings, text="  Настройки  ")

        self._update_company_label()

        self.status = tk.Label(self, text="Готово", anchor="w",
                               bg=C["bg2"], fg=C["text"], padx=10)
        self.status.pack(fill="x", side="bottom")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_logo_into(self, parent):
        for w in parent.winfo_children():
            w.destroy()
        logo_path_raw = (self.db.get_setting("logo_path", "") or "").strip()
        local_path = _resolve_logo(logo_path_raw) if logo_path_raw else None
        img = make_logo_image(local_path) if local_path else None
        if img is not None:
            lbl = tk.Label(parent, image=img, bg=C["yellow"])
            lbl.image = img
            lbl.pack()
            self._logo_img = img
        else:
            cv = tk.Canvas(parent, width=40, height=40,
                           bg=C["orange"], highlightthickness=0)
            cv.create_text(20, 20, text="ЗД", fill="white",
                           font=("Segoe UI", 14, "bold"))
            cv.pack()
            self._logo_img = None

    def _reload_logo(self):
        self._load_logo_into(self._logo_frame)

    def _update_company_label(self):
        name = self.db.get_setting("company_name", "Зушек Дрифт")
        self.lbl_company.configure(text=name)

    def _bind_hotkeys(self):
        self.bind_all("<Control-n>", lambda e: self._focus_tab(0))
        self.bind_all("<Control-f>", lambda e: self._focus_tab(2))
        self.bind_all("<F5>", lambda e: self.refresh_all())
        self.bind_all("<Delete>", self._on_delete_key)

    def _on_delete_key(self, event):
        idx = self.nb.index(self.nb.select())
        try:
            if idx == 1:
                self.tab_subs.delete_sub()
            elif idx == 3:
                self.tab_day.delete_visit()
        except Exception:
            pass

    def _focus_tab(self, idx):
        self.nb.select(idx)
        self.after(50, lambda: self.focus_force())

    def _backup(self):
        try:
            path = self.db.backup()
            messagebox.showinfo("Бэкап", f"Копия создана:\n{path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать бэкап:\n{e}")

    def _about(self):
        messagebox.showinfo(
            "О программе",
            f"Зушек Дрифт — система учёта клиентов и продаж\n"
            f"детского отдела развлечений.\n\n"
            f"Python + Tkinter + SQLite\n"
            f"Версия {CURRENT_VERSION}")

    def _on_close(self):
        if messagebox.askyesno("Выход", "Закрыть программу?"):
            try:
                self.db.close()
            except Exception:
                pass
            self.destroy()

    def refresh_all(self):
        self.status.configure(text="Обновление...")
        for tab in (self.tab_visit, self.tab_subs, self.tab_clients,
                    self.tab_day, self.tab_reports, self.tab_settings):
            try:
                if hasattr(tab, "reload"):
                    tab.reload()
            except Exception:
                logger.exception("Ошибка обновления вкладки")
        self._update_company_label()
        self._reload_logo()
        self.status.configure(text="Готово")


# ===========================================================================
# Запуск
# ===========================================================================
def main():
    # 1. Проверка обновлений ДО запуска GUI
    if AUTO_UPDATE_CHECK:
        try:
            check_for_updates(parent=None, silent_if_no_update=True)
        except Exception:
            logger.exception("Ошибка при проверке обновлений")

    # 2. Запуск приложения
    try:
        app = MainWindow()
        app.mainloop()
    except Exception as e:
        logger.exception("Критическая ошибка")
        messagebox.showerror("Критическая ошибка",
                             f"{e}\n\nПодробности в app.log")
        traceback.print_exc()


if __name__ == "__main__":
    main()
