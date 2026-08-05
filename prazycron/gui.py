from __future__ import annotations

import getpass
import json
import os
import shlex
import uuid
import subprocess
import threading
import shutil
import sys
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, font as tkfont, messagebox, simpledialog, ttk
from dataclasses import replace
from datetime import datetime
from typing import Callable

from . import APP_FULL_NAME, APP_TAGLINE, __version__
from .ai import AIError, explain as ai_explain, get_api_key, set_session_api_key
from .analyzer import analyze, humanize_schedule, validate_schedule
from .backend import BACKUP_DIR, BackendError, CronBackend
from .config import DEFAULTS
from .config import THEMES, apply_theme, load_config, save_config
from .i18n import LANGUAGES, Translator
from .model import CronEntry
from .diagnostics import diagnose
from .environment import KNOWN_ENV, parse_environment, update_environment
from .execution import HISTORY_DIR, read_history
from .metadata import MetadataStore, entry_signature
from .overlap import detect_conflicts, unwrap_prazycron
from .schedule import dst_note, next_runs, system_timezone_name
from .security import make_password_record, verify_password
from .systemd_timers import create_timer, delete_timer, list_timers, toggle_timer
from .validation import ValidationIssue, validate_entry

PROVIDER_KEYS = ("builtin", "ollama", "openai")
THEME_I18N_KEYS = {
    "dark": "theme_dark", "light": "theme_light", "blue": "theme_blue",
    "green": "theme_green", "high_contrast": "theme_high_contrast",
}
COMMAND_TEMPLATE_SPECS = (
    ("template_rsync", "/usr/bin/rsync -a --delete /path/source/ /path/destination/"),
    ("template_apt", "/usr/bin/apt-get update && /usr/bin/apt-get -y upgrade"),
    ("template_cleanup", "/usr/bin/find /path -type f -mtime +30 -delete"),
    ("template_logrotate", "/usr/bin/find /var/log/my-program -type f -name '*.log' -mtime +14 -delete"),
    ("template_url", "/usr/bin/curl --fail --silent --show-error https://example.com/"),
)
PRESET_SPECS = (
    ("every_minute", "* * * * *"), ("hourly", "0 * * * *"), ("daily", "0 3 * * *"),
    ("weekly", "0 3 * * 0"), ("monthly", "0 3 1 * *"), ("at_reboot", "@reboot"),
)


def calculate_tree_row_height(configured_height: int, font_linespace: int, status_image_height: int = 0) -> int:
    """Return a row height that never clips the selected interface font or status badge."""
    text_height = max(1, int(font_linespace)) + 16
    badge_height = max(0, int(status_image_height)) + 12 if status_image_height else 0
    return max(32, int(configured_height), text_height, badge_height)


class PrazyCronGUI:
    def __init__(self, root: tk.Tk, smoke_test: bool = False) -> None:
        self.root = root
        self.cfg = load_config()
        self.tr = Translator(str(self.cfg.get("language", "en")))
        self.backend = CronBackend()
        self.entries: list[CronEntry] = []
        self.filtered_entries: list[CronEntry] = []
        self.sort_column = "schedule"
        self.sort_reverse = False
        self.source_filter = ""
        self._provider_display_to_key: dict[str, str] = {}
        self._status_after_id: str | None = None
        self.analysis_visible = False
        self.view_mode = "cron"
        self.metadata_store = MetadataStore()
        self._session_unlocked = False
        self._load_icon()
        self._load_status_images()
        self._configure_root()
        self._build_ui()
        self.refresh()
        if smoke_test:
            self.root.after(1200, self.root.destroy)

    def _load_icon(self) -> None:
        candidates = [
            Path("/usr/share/icons/hicolor/256x256/apps/prazycron.png"),
            Path(__file__).resolve().parent.parent / "assets" / "prazycron.png",
        ]
        for path in candidates:
            if path.exists():
                try:
                    self._icon = tk.PhotoImage(file=str(path))
                    self.root.iconphoto(True, self._icon)
                    return
                except tk.TclError:
                    continue

    def _load_status_images(self) -> None:
        candidates = [
            Path("/usr/share/prazycron"),
            Path(__file__).resolve().parent.parent / "assets",
        ]
        self.status_on_image: tk.PhotoImage | None = None
        self.status_off_image: tk.PhotoImage | None = None
        for folder in candidates:
            try:
                on_path = folder / "status-on.png"
                off_path = folder / "status-off.png"
                if on_path.exists() and off_path.exists():
                    self.status_on_image = tk.PhotoImage(file=str(on_path))
                    self.status_off_image = tk.PhotoImage(file=str(off_path))
                    return
            except tk.TclError:
                continue

    def _configure_root(self) -> None:
        self.root.title(f"{APP_FULL_NAME} {__version__} — {APP_TAGLINE}")
        self.root.geometry("1580x860")
        self.root.minsize(1100, 650)
        self.root.option_add("*tearOff", False)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def _colors(self) -> dict[str, str]:
        return dict(self.cfg.get("colors", DEFAULTS["colors"]))

    def _configure_styles(self) -> None:
        c = self._colors()
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        family = str(self.cfg.get("font_family", "Noto Sans"))
        size = int(self.cfg.get("font_size", 11))
        self.interface_font = tkfont.Font(root=self.root, family=family, size=size)
        self.interface_bold_font = tkfont.Font(root=self.root, family=family, size=size, weight="bold")
        linespace = int(self.interface_font.metrics("linespace"))
        badge_height = max(
            self.status_on_image.height() if self.status_on_image is not None else 0,
            self.status_off_image.height() if self.status_off_image is not None else 0,
        )
        row_height = calculate_tree_row_height(int(self.cfg.get("row_height", 40)), linespace, badge_height)
        self.computed_row_height = row_height
        self.root.configure(bg=c["window"])
        self.root.option_add("*Font", (family, size))
        style.configure(".", background=c["window"], foreground=c["text"], fieldbackground=c["field"],
                        bordercolor=c["grid"], lightcolor=c["grid"], darkcolor=c["grid"],
                        font=(family, size))
        style.configure("TFrame", background=c["window"])
        style.configure("Panel.TFrame", background=c["panel"])
        style.configure("Card.TFrame", background=c["panel"], borderwidth=1, relief="solid")
        style.configure("TLabel", background=c["window"], foreground=c["text"])
        style.configure("Muted.TLabel", background=c["window"], foreground=c["muted"])
        style.configure("Panel.TLabel", background=c["panel"], foreground=c["text"])
        style.configure("Accent.TLabel", background=c["window"], foreground=c["accent"], font=(family, size, "bold"))
        style.configure("PanelAccent.TLabel", background=c["panel"], foreground=c["accent"], font=(family, size, "bold"))
        style.configure("TButton", background=c["panel"], foreground=c["text"], borderwidth=1, padding=(12, 8), relief="flat")
        style.map("TButton", background=[("active", c["header"]), ("pressed", c["accent"])], foreground=[("disabled", c["muted"])])
        style.configure("Toolbar.TButton", background=c["panel"], foreground=c["text"], borderwidth=1, padding=(6, 5), relief="solid", font=(family, max(9, size - 1)))
        style.map("Toolbar.TButton", background=[("active", c["header"]), ("pressed", c["selected"])])
        style.configure("Danger.TButton", background=c["panel"], foreground="#ff6b6b", borderwidth=1, padding=(6, 5), relief="solid", font=(family, max(9, size - 1)))
        style.map("Danger.TButton", background=[("active", c["header"]), ("pressed", "#7a2830")])
        style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff", padding=(12, 7))
        style.configure("Stepper.TButton", background=c["header"], foreground=c["text"],
                        borderwidth=1, padding=(10, 7), relief="solid", font=(family, max(12, size + 1), "bold"))
        style.map("Stepper.TButton", background=[("active", c["accent"]), ("pressed", c["selected"])],
                  foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
        style.map("Accent.TButton", background=[("active", c["selected"])])
        style.configure("TEntry", fieldbackground=c["field"], foreground=c["text"], insertcolor=c["text"], padding=5)
        style.configure("TCombobox", fieldbackground=c["field"], foreground=c["text"], arrowsize=18, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["text"])])
        style.configure("TCheckbutton", background=c["window"], foreground=c["text"])
        style.map("TCheckbutton", background=[("active", c["window"])])
        style.configure("Treeview", background=c["row_even"], fieldbackground=c["row_even"], foreground=c["text"],
                        rowheight=row_height, borderwidth=1, relief="solid", font=(family, size))
        style.configure("Treeview.Heading", background=c["header"], foreground=c["text"],
                        padding=(10, 8), relief="raised", borderwidth=1, font=(family, size, "bold"))
        style.map("Treeview", background=[("selected", c["selected"])], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", c["accent"])])
        style.configure("Visible.Vertical.TScrollbar", background=c["header"], troughcolor=c["field"],
                        bordercolor=c["grid"], arrowcolor=c["text"], arrowsize=22, borderwidth=2, width=18)
        style.configure("Visible.Horizontal.TScrollbar", background=c["header"], troughcolor=c["field"],
                        bordercolor=c["grid"], arrowcolor=c["text"], arrowsize=22, borderwidth=2, width=18)
        style.configure("Strong.TNotebook", background=c["panel"], borderwidth=2, relief="solid")
        style.configure("Strong.TNotebook.Tab", background=c["header"], foreground=c["muted"],
                        padding=(18, 10), borderwidth=2, font=(family, size, "bold"))
        style.map("Strong.TNotebook.Tab", background=[("selected", c["accent"]), ("active", c["selected"])],
                  foreground=[("selected", "#ffffff"), ("active", "#ffffff")])
        style.configure("TLabelframe", background=c["window"], foreground=c["text"], bordercolor=c["grid"], borderwidth=1)
        style.configure("TLabelframe.Label", background=c["window"], foreground=c["text"], font=(family, size, "bold"))
        style.configure("Status.TLabel", background=c["panel"], foreground=c["text"], padding=(8, 6))

    def _build_ui(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        self.root.config(menu="")
        self.analysis_visible = False
        self._configure_styles()
        c = self._colors()

        self.app_menu = tk.Menu(self.root, bg=c["panel"], fg=c["text"], activebackground=c["accent"], activeforeground="#fff")
        self.app_menu.add_command(label=self.tr("settings"), command=self.open_settings)
        self.app_menu.add_command(label=self.tr("backups"), command=self.open_backups)
        self.app_menu.add_command(label=self.tr("launch_tui"), command=self.launch_tui)
        self.app_menu.add_separator()
        self.app_menu.add_command(label=self.tr("next_runs"), command=self.show_next_runs)
        self.app_menu.add_command(label=self.tr("execution_history"), command=self.show_history)
        self.app_menu.add_command(label=self.tr("environment"), command=self.edit_environment)
        self.app_menu.add_command(label=self.tr("diagnostics"), command=self.open_diagnostics)
        self.app_menu.add_command(label=self.tr("conflicts_duplicates"), command=self.show_conflicts)
        self.app_menu.add_command(label=self.tr("export_task"), command=self.export_selected)
        self.app_menu.add_command(label=self.tr("import_task"), command=self.import_task)
        self.app_menu.add_command(label=self.tr("table_columns"), command=self.configure_columns)
        self.app_menu.add_command(label=self.tr("readonly_lock"), command=self.open_security_settings)
        self.app_menu.add_separator()
        self.app_menu.add_command(label=f"{APP_FULL_NAME} {__version__}", command=self.show_about)
        self.app_menu.add_command(label=self.tr("quit"), command=self.root.destroy)

        outer = ttk.Frame(self.root, padding=(12, 10, 12, 10))
        outer.pack(fill="both", expand=True)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="☰", width=2, style="Toolbar.TButton", command=self._show_app_menu).pack(side="left", padx=(0, 9))
        button_specs = [
            ("⟳", "refresh", self.refresh, "Toolbar.TButton"),
            ("⊕", "add", self.add_entry, "Toolbar.TButton"),
            ("✎", "edit", self.edit_selected, "Toolbar.TButton"),
            ("⏻", "toggle", self.toggle_selected, "Toolbar.TButton"),
            ("⧉", "duplicate", self.duplicate_selected, "Toolbar.TButton"),
            ("🗑", "delete", self.delete_selected, "Danger.TButton"),
            ("</>", "source_preview", self.preview_selected, "Toolbar.TButton"),
            ("✣", "explain", self.explain_selected, "Toolbar.TButton"),
        ]
        actions_frame = ttk.Frame(toolbar)
        actions_frame.pack(side="left", fill="x", expand=True)
        wrap_actions = int(self.cfg.get("font_size", 11)) >= 14
        for index, (icon, key, command, style_name) in enumerate(button_specs):
            button = ttk.Button(actions_frame, text=f"{icon}  {self.tr(key)}", command=command, style=style_name)
            if wrap_actions:
                button.grid(row=index // 4, column=index % 4, sticky="ew", padx=(0, 4), pady=(0, 4))
                actions_frame.columnconfigure(index % 4, weight=1)
            else:
                button.pack(side="left", padx=(0, 4))

        # From font size 12 upward, utility controls use their own row. This prevents
        # the Backups button from being compressed to one letter or disappearing.
        large_font_toolbar = int(self.cfg.get("font_size", 11)) >= 12
        tools = ttk.Frame(outer if large_font_toolbar else toolbar)
        if large_font_toolbar:
            tools.pack(fill="x", pady=(0, 8))
            tools_side = "left"
        else:
            tools.pack(side="right")
            tools_side = "right"
        tool_specs = [
            ("⚙", "settings", self.open_settings),
            ("▣", "launch_tui", self.launch_tui),
            ("▤", "backups", self.open_backups),
        ]
        for icon, key, command in tool_specs:
            label = f"{icon}  {self.tr(key)}"
            # A character width gives each utility button a stable minimum size even
            # when the interface font is enlarged.
            ttk.Button(
                tools, text=label, command=command, style="Toolbar.TButton",
                width=max(14, len(self.tr(key)) + 4),
            ).pack(side=tools_side, padx=(4, 0) if tools_side == "right" else (0, 4))

        featurebar = ttk.Frame(outer)
        featurebar.pack(fill="x", pady=(0, 7))
        feature_specs = [
            ("▶", self.tr("run_now"), self.run_selected_now),
            ("◷", self.tr("next_runs"), self.show_next_runs),
            ("▥", self.tr("history"), self.show_history),
            ("✓", self.tr("validation"), self.validate_selected),
            ("⚕", self.tr("diagnostics"), self.open_diagnostics),
            ("≋", self.tr("environment"), self.edit_environment),
            ("★", self.tr("name_tags"), self.edit_metadata),
        ]
        wrap_features = int(self.cfg.get("font_size", 11)) >= 12
        for index, (icon, label, command) in enumerate(feature_specs):
            button = ttk.Button(featurebar, text=f"{icon}  {label}", command=command, style="Toolbar.TButton")
            if wrap_features:
                button.grid(row=index // 4, column=index % 4, sticky="ew", padx=(0, 4), pady=(0, 4))
                featurebar.columnconfigure(index % 4, weight=1)
            else:
                button.pack(side="left", padx=(0, 4))

        admin = ttk.Frame(outer)
        admin.pack(fill="x", pady=(0, 10))
        ttk.Label(admin, text=self.tr("administration")).pack(side="left")
        ttk.Button(admin, text=f"⇩  {self.tr('load_root')}", command=self.load_root, style="Toolbar.TButton").pack(side="left", padx=8)
        ttk.Button(admin, text=f"▷  {self.tr('start_service')}", command=self.start_service, style="Toolbar.TButton").pack(side="left", padx=(0, 24))
        ttk.Label(admin, text=self.tr("provider")).pack(side="left", padx=(8, 6))
        provider_values = [self.tr(key) for key in PROVIDER_KEYS]
        self._provider_display_to_key = {self.tr(key): key for key in PROVIDER_KEYS}
        current_key = str(self.cfg.get("provider", "builtin"))
        self.provider_var = tk.StringVar(value=self.tr(current_key if current_key in PROVIDER_KEYS else "builtin"))
        self.provider_combo = ttk.Combobox(admin, textvariable=self.provider_var, values=provider_values, state="readonly", width=32)
        self.provider_combo.pack(side="left")
        self.provider_combo.bind("<<ComboboxSelected>>", self._provider_changed)
        self.api_note_label = ttk.Label(admin, text="ⓘ " + self.tr("api_note"), style="Muted.TLabel")
        self._update_api_notice()
        ttk.Label(admin, text=self.tr("view")).pack(side="left", padx=(22, 6))
        self._view_display_to_key = {self.tr("cron_view"): "cron", self.tr("systemd_timers"): "systemd"}
        self.mode_var = tk.StringVar(value=self.tr("cron_view") if self.view_mode == "cron" else self.tr("systemd_timers"))
        mode_combo = ttk.Combobox(admin, textvariable=self.mode_var, values=list(self._view_display_to_key), state="readonly", width=18)
        mode_combo.pack(side="left")
        mode_combo.bind("<<ComboboxSelected>>", self._view_mode_changed)

        self.main_pane = ttk.Panedwindow(outer, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True)
        self.left_panel = ttk.Frame(self.main_pane, style="Card.TFrame", padding=1)
        self.right_panel = ttk.Frame(self.main_pane, style="Card.TFrame", padding=12)
        self.main_pane.add(self.left_panel, weight=5)

        filters = ttk.Frame(self.left_panel, padding=(12, 10))
        filters.pack(fill="x")
        ttk.Label(filters, text=self.tr("search")).pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(filters, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(7, 18))
        self.search_var.trace_add("write", lambda *_: self.apply_filters())
        ttk.Label(filters, text=self.tr("source")).pack(side="left")
        self.source_var = tk.StringVar(value=self.tr("all_sources"))
        self.source_combo = ttk.Combobox(filters, textvariable=self.source_var, state="readonly", width=28)
        self.source_combo.pack(side="left", padx=(7, 0))
        self.source_combo.bind("<<ComboboxSelected>>", lambda _e: self.apply_filters())

        table_frame = ttk.Frame(self.left_panel, style="Panel.TFrame")
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = ("favorite", "name", "schedule", "user", "source", "last_run", "next_run", "command")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", selectmode="browse")
        self.tree.column("#0", width=110, minwidth=104, stretch=False, anchor="center")
        self.tree.heading("#0", text=self._heading_text("state"), command=lambda: self.sort_by("state"))
        widths = {"favorite": 52, "name": 155, "schedule": 280, "user": 120, "source": 195, "last_run": 155, "next_run": 155, "command": 560}
        labels = {"favorite": "★", "name": self.tr("name_tags_column"), "last_run": self.tr("last_run"), "next_run": self.tr("next_run")}
        anchors = {column: "w" for column in columns}; anchors["favorite"] = "center"
        visible_columns = list(self.cfg.get("visible_columns", ["state", *columns]))
        self.tree.configure(displaycolumns=tuple(col for col in columns if col in visible_columns))
        for column in columns:
            self.tree.column(column, width=widths[column], minwidth=48 if column == "favorite" else 100, stretch=column == "command", anchor=anchors[column])
            heading = labels.get(column, self._heading_text(column))
            self.tree.heading(column, text=heading if column in {"favorite", "name", "last_run", "next_run"} else self._heading_text(column), anchor=anchors[column], command=lambda c=column: self.sort_by(c))
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", style="Visible.Vertical.TScrollbar", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", style="Visible.Horizontal.TScrollbar", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.tree.tag_configure("even", background=c["row_even"], foreground=c["text"])
        self.tree.tag_configure("odd", background=c["row_odd"], foreground=c["text"])
        self.tree.tag_configure("off_even", background=c["row_even"], foreground=c["muted"])
        self.tree.tag_configure("off_odd", background=c["row_odd"], foreground=c["muted"])
        self.tree.tag_configure("warning", foreground="#ffd166")
        self.tree.tag_configure("error", foreground="#ff7777")
        self.tree.bind("<Double-1>", lambda _e: self.edit_selected())
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Return>", lambda _e: self.edit_selected())
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)

        self.context_menu = tk.Menu(self.root, bg=c["panel"], fg=c["text"], activebackground=c["accent"], activeforeground="#fff")
        self.context_menu.add_command(label=self.tr("edit"), command=self.edit_selected)
        self.context_menu.add_command(label=self.tr("toggle"), command=self.toggle_selected)
        self.context_menu.add_command(label=self.tr("duplicate"), command=self.duplicate_selected)
        self.context_menu.add_command(label=self.tr("delete"), command=self.delete_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label=self.tr("context_copy"), command=self.copy_selected_command)
        self.context_menu.add_command(label=self.tr("context_source"), command=self.preview_selected)
        self.context_menu.add_command(label=self.tr("context_explain"), command=self.explain_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label=self.tr("run_now"), command=self.run_selected_now)
        self.context_menu.add_command(label=self.tr("next_runs"), command=self.show_next_runs)
        self.context_menu.add_command(label=self.tr("execution_history"), command=self.show_history)
        self.context_menu.add_command(label=self.tr("metadata_title"), command=self.edit_metadata)
        self.context_menu.add_command(label=self.tr("export"), command=self.export_selected)

        status_frame = ttk.Frame(self.left_panel, style="Panel.TFrame", padding=(8, 3))
        status_frame.pack(fill="x")
        self.status_var = tk.StringVar(value=self.tr("status_ready"))
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(status_frame, text=self.tr("backup_folder"), command=self.open_backup_folder).pack(side="right", padx=4, pady=3)

        self._build_analysis_panel(self.right_panel)

        self.root.bind("<Control-r>", lambda _e: self.refresh())
        self.root.bind("<Control-n>", lambda _e: self.add_entry())
        self.root.bind("<Control-comma>", lambda _e: self.open_settings())
        self.root.bind("<F6>", lambda _e: self.explain_selected())
        self._attach_resize_grip(self.root)

    def _show_app_menu(self) -> None:
        try:
            self.app_menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            self.app_menu.grab_release()

    def _build_analysis_panel(self, parent: ttk.Frame) -> None:
        c = self._colors()
        header = ttk.Frame(parent, style="Panel.TFrame")
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="✣  " + self.tr("selected_explanation"), style="PanelAccent.TLabel").pack(side="left")
        ttk.Button(header, text="✕", width=3, command=self._hide_analysis_panel, style="Toolbar.TButton").pack(side="right", padx=(5, 0))
        ttk.Button(header, text=self.tr("copy"), command=lambda: self._copy_text(getattr(self, "analysis_full_text", ""))).pack(side="right")
        self.analysis_task_var = tk.StringVar(value=self.tr("select_for_explanation"))
        self.analysis_meta_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.analysis_task_var, style="Panel.TLabel", wraplength=560, justify="left").pack(fill="x", pady=(0, 4))
        ttk.Label(parent, textvariable=self.analysis_meta_var, style="Muted.TLabel", wraplength=560, justify="left").pack(fill="x", pady=(0, 8))

        holder = ttk.Frame(parent, style="Panel.TFrame")
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, bg=c["panel"], highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(holder, orient="vertical", style="Visible.Vertical.TScrollbar", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        inner = tk.Frame(canvas, bg=c["panel"])
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        titles = [
            ("◷", self.tr("section_schedule")), ("➤", self.tr("section_action")),
            ("▣", self.tr("section_frequency")), ("▤", self.tr("section_impact")),
            ("⚠", self.tr("section_risks")), ("💡", self.tr("section_suggestion")),
        ]
        self.analysis_section_labels = []
        scroll_widgets: list[tk.Widget] = []
        for icon, title in titles:
            card = tk.Frame(inner, bg=c["field"], highlightbackground=c["grid"], highlightthickness=1, padx=14, pady=11)
            card.pack(fill="x", pady=(0, 9), padx=(0, 5))
            title_label = tk.Label(card, text=f"{icon}  {title}", bg=c["field"], fg=c["accent"], font=(str(self.cfg.get("font_family", "Noto Sans")), int(self.cfg.get("font_size", 11)), "bold"), anchor="w")
            title_label.pack(fill="x", pady=(0, 6))
            body = tk.Label(card, text="—", bg=c["field"], fg=c["text"], justify="left", anchor="nw", wraplength=500)
            body.pack(fill="x")
            self.analysis_section_labels.append(body)
            scroll_widgets.extend((card, title_label, body))

        def resize(_event: object = None) -> None:
            width = max(320, canvas.winfo_width())
            canvas.itemconfigure(window_id, width=width)
            for label in self.analysis_section_labels:
                label.configure(wraplength=max(260, width - 52))

        def wheel(event: tk.Event) -> str:
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")
            else:
                delta = int(-1 * (getattr(event, "delta", 0) / 120))
                canvas.yview_scroll(delta * 3, "units")
            return "break"

        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", resize)
        for widget in (canvas, inner, *scroll_widgets):
            widget.bind("<MouseWheel>", wheel)
            widget.bind("<Button-4>", wheel)
            widget.bind("<Button-5>", wheel)
        self.analysis_canvas = canvas
        self.analysis_full_text = ""

    def _show_analysis_panel(self) -> None:
        if not self.analysis_visible:
            self.main_pane.add(self.right_panel, weight=3)
            self.analysis_visible = True
        # Force geometry calculation immediately. Without this, some Tk builds leave the
        # newly added pane at zero width until another selection event occurs.
        self.root.update_idletasks()

        def place_sash() -> None:
            if not self.analysis_visible or not self.main_pane.winfo_exists():
                return
            total = max(1, self.main_pane.winfo_width())
            target = max(520, min(total - 360, int(total * 0.60)))
            try:
                self.main_pane.sashpos(0, target)
            except tk.TclError:
                pass
            self.right_panel.tkraise()

        place_sash()
        self.root.after_idle(place_sash)
        self.root.after(40, place_sash)

    def _hide_analysis_panel(self) -> None:
        if not self.analysis_visible:
            return
        try:
            self.main_pane.forget(self.right_panel)
        except tk.TclError:
            pass
        self.analysis_visible = False

    def _selection_changed(self, _event: object = None) -> None:
        entry = self.selected_entry()
        if not entry or not self.analysis_visible:
            return
        provider = self._provider_display_to_key.get(self.provider_var.get(), str(self.cfg.get("provider", "builtin")))
        if provider == "builtin":
            self._show_builtin_analysis(entry)

    def _show_builtin_analysis(self, entry: CronEntry) -> None:
        result = analyze(entry, language=str(self.cfg.get("language", "en")))
        self._render_analysis(entry, result.sections, result.as_text())

    def _render_analysis(self, entry: CronEntry, sections: dict[str, str], full_text: str) -> None:
        self.analysis_task_var.set(f"{self.tr('task_label')} {entry.command}")
        self.analysis_meta_var.set(f"{self.tr('schedule')}: {humanize_schedule(entry.schedule, str(self.cfg.get('language', 'pl')))}  [{entry.schedule}]   |   {self.tr('user')}: {entry.user}   |   {self.tr('source')[:-1]}: {entry.source}")
        values = list(sections.values())
        for index, label in enumerate(self.analysis_section_labels):
            label.configure(text=values[index] if index < len(values) else "—")
        self.analysis_full_text = full_text
        if hasattr(self, "analysis_canvas"):
            self.analysis_canvas.yview_moveto(0.0)

    def _render_external_analysis(self, entry: CronEntry, text: str) -> None:
        headings = [
            ("section_schedule", ["znaczenie harmonogramu", "schedule meaning"]),
            ("section_action", ["co robi zadanie", "what the task does"]),
            ("section_frequency", ["częstotliwość", "frequency"]),
            ("section_impact", ["wpływ na system", "system impact"]),
            ("section_risks", ["ryzyka / uwagi", "risks / notes", "warnings"]),
            ("section_suggestion", ["sugestia", "recommendation"]),
        ]
        lines = text.splitlines()
        parsed: dict[int, list[str]] = {}
        current: int | None = None
        for raw in lines:
            line = raw.strip().strip("#*: ")
            matched = None
            for idx, (_key, aliases) in enumerate(headings):
                if line.casefold() in aliases:
                    matched = idx
                    break
            if matched is not None:
                current = matched
                parsed.setdefault(current, [])
            elif current is not None and raw.strip():
                parsed.setdefault(current, []).append(raw.strip())
        self.analysis_task_var.set(f"{self.tr('task_label')} {entry.command}")
        self.analysis_meta_var.set(f"{self.tr('schedule')}: {humanize_schedule(entry.schedule, str(self.cfg.get('language', 'pl')))}  [{entry.schedule}]   |   {self.tr('user')}: {entry.user}   |   {self.tr('source')[:-1]}: {entry.source}")
        if parsed:
            for idx, label in enumerate(self.analysis_section_labels):
                label.configure(text="\n".join(parsed.get(idx, [])) or "—")
        else:
            self.analysis_section_labels[0].configure(text=text)
            for label in self.analysis_section_labels[1:]:
                label.configure(text="—")
        self.analysis_full_text = text

    def _update_api_notice(self) -> None:
        if not hasattr(self, "api_note_label"):
            return
        key = self._provider_display_to_key.get(self.provider_var.get(), str(self.cfg.get("provider", "builtin")))
        self.api_note_label.pack_forget()
        if key == "openai":
            self.api_note_label.pack(side="left", padx=10)

    def launch_tui(self) -> None:
        app = shutil.which("prazycron")
        base = [app, "--tui"] if app else [sys.executable, "-m", "prazycron.main", "--tui"]
        candidates: list[list[str]] = []
        if shutil.which("x-terminal-emulator"):
            candidates.append(["x-terminal-emulator", "-e", *base])
        if shutil.which("gnome-terminal"):
            candidates.append(["gnome-terminal", "--", *base])
        if shutil.which("xfce4-terminal"):
            candidates.append(["xfce4-terminal", "-x", *base])
        if shutil.which("konsole"):
            candidates.append(["konsole", "-e", *base])
        if shutil.which("xterm"):
            candidates.append(["xterm", "-e", *base])
        for command in candidates:
            try:
                subprocess.Popen(command, start_new_session=True)
                self._set_status(self.tr("tui_started"), temporary=True)
                return
            except OSError:
                continue
        messagebox.showerror(self.tr("error"), self.tr("terminal_not_found"), parent=self.root)

    def _provider_changed(self, _event: object = None) -> None:
        key = self._provider_display_to_key.get(self.provider_var.get(), "builtin")
        self.cfg["provider"] = key
        save_config(self.cfg)
        self._update_api_notice()
        if key == "openai" and not get_api_key():
            key_value = simpledialog.askstring(self.tr("api_prompt_title"), self.tr("api_prompt"), show="*", parent=self.root)
            if key_value:
                set_session_api_key(key_value)
            else:
                self.cfg["provider"] = "builtin"
                self.provider_var.set(self.tr("builtin"))
                save_config(self.cfg)
                self._update_api_notice()

    def _entry_metadata(self, entry: CronEntry) -> dict[str, object]:
        return self.metadata_store.get(entry)

    def _display_command(self, entry: CronEntry) -> str:
        meta = self._entry_metadata(entry)
        return str(meta.get("original_command") or unwrap_prazycron(entry.command))

    def _last_run_text(self, entry: CronEntry) -> str:
        records = read_history(str(self._entry_metadata(entry).get("entry_id") or entry_signature(entry)), limit=1)
        if not records:
            return "—"
        try:
            dt = datetime.fromisoformat(records[0].finished).astimezone()
            return dt.strftime("%Y-%m-%d %H:%M") + ("  ✓" if records[0].exit_code == 0 else f"  ✕{records[0].exit_code}")
        except ValueError:
            return records[0].finished[:16]

    def _next_run_text(self, entry: CronEntry) -> str:
        if entry.source_type.startswith("systemd_"):
            return str(entry.metadata.get("next", "—"))
        meta = self._entry_metadata(entry)
        try:
            runs = next_runs(entry.schedule, 1, timezone_name=str(meta.get("timezone") or system_timezone_name()))
            return runs[0].strftime("%Y-%m-%d %H:%M") if runs else "po uruchomieniu systemu"
        except ValueError:
            return "błąd harmonogramu"

    def _heading_text(self, column: str) -> str:
        custom = {"favorite": "★", "name": self.tr("name_tags_column"), "last_run": self.tr("last_run"), "next_run": self.tr("next_run")}
        label = custom.get(column, self.tr(column))
        if column == self.sort_column:
            return f"{label} {'▼' if self.sort_reverse else '▲'}"
        return f"{label} ↕"

    def sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.tree.heading("#0", text=self._heading_text("state"), command=lambda: self.sort_by("state"))
        for col in ("favorite", "name", "schedule", "user", "source", "last_run", "next_run", "command"):
            self.tree.heading(col, text=self._heading_text(col), command=lambda c=col: self.sort_by(c))
        self.apply_filters()

    def _sort_key(self, entry: CronEntry) -> object:
        meta = self._entry_metadata(entry)
        if self.sort_column == "state": return 0 if entry.enabled else 1
        if self.sort_column == "favorite": return 0 if meta.get("favorite") else 1
        if self.sort_column == "name": return str(meta.get("name", "")).casefold()
        if self.sort_column == "schedule": return humanize_schedule(entry.schedule, str(self.cfg.get("language", "en"))).casefold()
        if self.sort_column == "user": return entry.user.casefold()
        if self.sort_column == "source": return entry.source.casefold()
        if self.sort_column == "last_run": return self._last_run_text(entry)
        if self.sort_column == "next_run": return self._next_run_text(entry)
        return self._display_command(entry).casefold()

    def _view_mode_changed(self, _event: object = None) -> None:
        self.view_mode = self._view_display_to_key.get(self.mode_var.get(), "cron")
        self.refresh()

    def refresh(self) -> None:
        self._set_status(self.tr("refresh") + "…")
        try:
            self.metadata_store.load()
            self.entries = list_timers(include_system=True) if self.view_mode == "systemd" else self.backend.load()
            sources = [self.tr("all_sources")] + sorted({e.source for e in self.entries}, key=str.casefold)
            current = self.source_var.get() if hasattr(self, "source_var") else self.tr("all_sources")
            self.source_combo.configure(values=sources)
            self.source_var.set(current if current in sources else self.tr("all_sources"))
            self.apply_filters()
            self._update_status()
        except Exception as exc:
            self._show_error(exc)

    def apply_filters(self) -> None:
        query = self.search_var.get().strip().casefold() if hasattr(self, "search_var") else ""
        source = self.source_var.get() if hasattr(self, "source_var") else self.tr("all_sources")
        entries = self.entries
        if source != self.tr("all_sources"):
            entries = [e for e in entries if e.source == source]
        if query:
            def haystack(entry: CronEntry) -> str:
                meta = self._entry_metadata(entry)
                return " ".join((entry.schedule, humanize_schedule(entry.schedule, str(self.cfg.get("language", "en"))), self._display_command(entry), entry.user, entry.source, str(meta.get("name", "")), " ".join(meta.get("tags", []) if isinstance(meta.get("tags"), list) else []))).casefold()
            entries = [e for e in entries if query in haystack(e)]
        entries = sorted(entries, key=self._sort_key, reverse=self.sort_reverse)
        self.filtered_entries = entries
        current = self.selected_entry()
        selection_key = current.key if current else None
        self.tree.delete(*self.tree.get_children())
        selected_iid = None
        for index, entry in enumerate(entries):
            stripe = "even" if index % 2 == 0 else "odd"
            tag = stripe if entry.enabled else f"off_{stripe}"
            iid = entry.key
            use_pills = self.status_on_image is not None and self.status_off_image is not None
            image = self.status_on_image if entry.enabled else self.status_off_image
            meta = self._entry_metadata(entry)
            tags = meta.get("tags", []) if isinstance(meta.get("tags"), list) else []
            name_tags = str(meta.get("name", ""))
            if tags:
                name_tags += ("  " if name_tags else "") + "#" + " #".join(str(tag) for tag in tags)
            severity_tag = ""
            if not entry.source_type.startswith("systemd_"):
                display_command = self._display_command(entry).lower()
                if validate_schedule(entry.schedule):
                    severity_tag = "error"
                elif any(token in display_command for token in ("rm -rf /", "| sh", "| bash", "chmod 777")):
                    severity_tag = "warning"
                if severity_tag:
                    name_tags = ("✕ " if severity_tag == "error" else "⚠ ") + (name_tags or self.tr("needs_attention"))
            values = (
                "★" if meta.get("favorite") else "☆", name_tags or "—",
                entry.schedule if entry.source_type.startswith("systemd_") else humanize_schedule(entry.schedule, str(self.cfg.get("language", "en"))),
                entry.user, entry.source, self._last_run_text(entry), self._next_run_text(entry), self._display_command(entry),
            )
            self.tree.insert("", "end", iid=iid, text="" if use_pills else (self.tr("on") if entry.enabled else self.tr("off")), image=image if use_pills else "", values=values, tags=(tag, severity_tag) if severity_tag else (tag,))
            if entry.key == selection_key:
                selected_iid = iid
        if selected_iid:
            self.tree.selection_set(selected_iid); self.tree.see(selected_iid)
        elif entries:
            first_iid = entries[0].key
            self.tree.selection_set(first_iid); self.tree.focus(first_iid); self.tree.see(first_iid)

    def selected_entry(self) -> CronEntry | None:
        if not hasattr(self, "tree"):
            return None
        selected = self.tree.selection()
        if not selected:
            return None
        key = selected[0]
        return next((e for e in self.entries if e.key == key), None)

    def _require_selection(self) -> CronEntry | None:
        entry = self.selected_entry()
        if not entry:
            messagebox.showinfo(self.tr("information"), self.tr("no_selection"), parent=self.root)
        return entry

    def _show_context_menu(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def add_entry(self) -> None:
        if not self._authorize_change():
            return
        if self.view_mode == "systemd":
            self._systemd_editor(None)
        else:
            self._entry_editor(None)

    def edit_selected(self) -> None:
        entry = self._require_selection()
        if not entry or not self._authorize_change():
            return
        if entry.source_type.startswith("systemd_"):
            self._systemd_editor(entry)
            return
        if not entry.editable:
            messagebox.showinfo(self.tr("information"), self.tr("not_editable"), parent=self.root)
            self.preview_selected()
            return
        self._entry_editor(entry)

    def _managed_command(self, command: str, entry_id: str, history_enabled: bool, prevent_overlap: bool) -> str:
        if not history_enabled and not prevent_overlap:
            return command
        args = ["/usr/bin/prazycron-run", "--id", entry_id, "--history-dir", str(HISTORY_DIR), "--owner", getpass.getuser()]
        if prevent_overlap:
            args += ["--lock", entry_id]
        if not history_enabled:
            args += ["--no-history"]
        args += ["--", "/bin/sh", "-c", command]
        return shlex.join(args)

    def _entry_editor(self, entry: CronEntry | None) -> None:
        win = tk.Toplevel(self.root)
        win.title(self.tr("entry_editor"))
        win.geometry("900x720")
        win.minsize(720, 560)
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        destinations = self.backend.available_destinations(self.backend.root_loaded)
        def destination_label(item: tuple[str, str, str]) -> str:
            _backend_label, source_path, source_type = item
            if source_type == "user_crontab":
                return self.tr("destination_user")
            if source_type == "root_crontab":
                return self.tr("destination_root")
            if source_path == "/etc/crontab":
                return self.tr("destination_system")
            return source_path
        destination_labels = [destination_label(d) for d in destinations]
        destination_map = {destination_label(d): d for d in destinations}
        if entry:
            matched_item = next((d for d in destinations if d[1] == entry.source and d[2] == entry.source_type), None)
            matched = destination_label(matched_item) if matched_item else entry.source
        else:
            matched = destination_labels[0]
        meta = self._entry_metadata(entry) if entry else {}
        entry_id = str(meta.get("entry_id") or uuid.uuid4())
        original_command = str(meta.get("original_command") or (unwrap_prazycron(entry.command) if entry else ""))
        dest_var = tk.StringVar(value=matched)
        schedule_var = tk.StringVar(value=entry.schedule if entry else "0 3 * * *")
        user_var = tk.StringVar(value=entry.user if entry else getpass.getuser())
        enabled_var = tk.BooleanVar(value=entry.enabled if entry else True)
        name_var = tk.StringVar(value=str(meta.get("name", "")))
        tags_var = tk.StringVar(value=", ".join(str(x) for x in meta.get("tags", []) if isinstance(meta.get("tags"), list)))
        favorite_var = tk.BooleanVar(value=bool(meta.get("favorite", False)))
        history_var = tk.BooleanVar(value=bool(meta.get("history_enabled", False)))
        lock_var = tk.BooleanVar(value=bool(meta.get("prevent_overlap", False)))
        timezone_var = tk.StringVar(value=str(meta.get("timezone") or system_timezone_name()))

        ttk.Label(frame, text=self.tr("source_type")).grid(row=0, column=0, sticky="w", pady=5)
        dest_combo = ttk.Combobox(frame, textvariable=dest_var, values=destination_labels, state="disabled" if entry else "readonly")
        dest_combo.grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text=self.tr("presets")).grid(row=1, column=0, sticky="w", pady=5)
        preset_row = ttk.Frame(frame); preset_row.grid(row=1, column=1, sticky="ew", pady=5); preset_row.columnconfigure(0, weight=1); preset_row.columnconfigure(1, weight=1)
        preset_map = {self.tr(key): value for key, value in PRESET_SPECS}
        preset_var = tk.StringVar()
        preset_combo = ttk.Combobox(preset_row, textvariable=preset_var, values=list(preset_map), state="readonly")
        preset_combo.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        preset_combo.bind("<<ComboboxSelected>>", lambda _e: schedule_var.set(preset_map[preset_var.get()]))
        template_map = {self.tr(key): value for key, value in COMMAND_TEMPLATE_SPECS}
        template_var = tk.StringVar(value=self.tr("command_template"))
        template_combo = ttk.Combobox(preset_row, textvariable=template_var, values=[self.tr("command_template"), *template_map], state="readonly")
        template_combo.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(frame, text=self.tr("raw_schedule")).grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=schedule_var).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text=self.tr("user")).grid(row=3, column=0, sticky="w", pady=5)
        user_entry = ttk.Entry(frame, textvariable=user_var); user_entry.grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text=self.tr("task_name")).grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=name_var).grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text=self.tr("tags_csv")).grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=tags_var).grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text=self.tr("analysis_timezone")).grid(row=6, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=timezone_var).grid(row=6, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text=self.tr("command")).grid(row=7, column=0, sticky="nw", pady=5)
        command_text = tk.Text(frame, height=8, wrap="word", bg=self._colors()["field"], fg=self._colors()["text"], insertbackground=self._colors()["text"], relief="solid", borderwidth=1)
        command_text.grid(row=7, column=1, sticky="nsew", pady=5)
        frame.rowconfigure(7, weight=1)
        command_text.insert("1.0", original_command)
        template_combo.bind("<<ComboboxSelected>>", lambda _e: (command_text.delete("1.0", "end"), command_text.insert("1.0", template_map.get(template_var.get(), command_text.get("1.0", "end-1c")))))
        options = ttk.Frame(frame); options.grid(row=8, column=1, sticky="w", pady=5)
        ttk.Checkbutton(options, text=self.tr("enabled"), variable=enabled_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(options, text=self.tr("favorite"), variable=favorite_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(options, text=self.tr("record_history"), variable=history_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(options, text=self.tr("prevent_parallel"), variable=lock_var).pack(side="left")

        hint_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=hint_var, style="Muted.TLabel", wraplength=760, justify="left").grid(row=9, column=0, columnspan=2, sticky="ew", pady=(4, 2))

        def current_values() -> tuple[str, str, str, str]:
            label = dest_var.get()
            _name, source, source_type = destination_map[label]
            return source, source_type, schedule_var.get().strip(), command_text.get("1.0", "end-1c").strip()

        def update_hint(*_args: object) -> None:
            try:
                _source, _source_type, schedule, _command = current_values()
                runs = next_runs(schedule, 2, timezone_name=timezone_var.get().strip())
                text = self.tr("next_prefix", date=", ".join(run.strftime("%Y-%m-%d %H:%M %Z") for run in runs)) if runs else f"@reboot — {self.tr('after_reboot')}."
                note = dst_note(schedule, timezone_var.get().strip())
                hint_var.set(text + (("\n⚠ " + note) if note else ""))
            except (ValueError, StopIteration) as exc:
                hint_var.set("⚠ " + str(exc))
        schedule_var.trace_add("write", update_hint); timezone_var.trace_add("write", update_hint); update_hint()

        def destination_changed(*_args: object) -> None:
            selected = destination_map.get(dest_var.get(), destinations[0])
            user_entry.configure(state="normal" if selected[2] in {"system_file", "cron_d", "root_crontab"} else "disabled")
            if selected[2] == "root_crontab": user_var.set("root")
            elif selected[2] == "user_crontab": user_var.set(getpass.getuser())
        destination_changed(); dest_var.trace_add("write", destination_changed)

        action_row = ttk.Frame(frame); action_row.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(action_row, text=self.tr("check"), command=lambda: self._show_validation_issues(validate_entry(schedule_var.get().strip(), command_text.get("1.0", "end-1c").strip(), user_var.get().strip(), source_type=current_values()[1]), parent=win)).pack(side="left")
        ttk.Button(action_row, text=self.tr("show_5_runs"), command=lambda: self._show_next_runs_for(schedule_var.get().strip(), timezone_var.get().strip(), parent=win)).pack(side="left", padx=6)
        if entry:
            ttk.Button(action_row, text=self.tr("source_environment"), command=lambda: self.edit_environment(entry)).pack(side="left")
        ttk.Button(action_row, text=self.tr("cancel"), command=win.destroy).pack(side="right", padx=(6, 0))

        def save() -> None:
            try:
                source, source_type, schedule, command = current_values()
                issues = validate_entry(schedule, command, user_var.get().strip(), source_type=source_type)
                if any(issue.severity == "error" for issue in issues):
                    self._show_validation_issues(issues, parent=win); return
                if issues and not self._confirm_warnings(issues, parent=win):
                    return
                managed = self._managed_command(command, entry_id, history_var.get(), lock_var.get())
                if entry:
                    _before, _after, diff = self.backend.preview_edit(entry, schedule, managed, user_var.get().strip(), enabled_var.get())
                else:
                    _before, _after, diff = self.backend.preview_add(source, source_type, schedule, managed, user_var.get().strip(), enabled_var.get())
                if not self._confirm_diff(diff, parent=win):
                    return
                if entry:
                    self.backend.edit_entry(entry, schedule, managed, user_var.get().strip(), enabled_var.get())
                else:
                    self.backend.add_entry(source, source_type, schedule, managed, user_var.get().strip(), enabled_var.get())
                fresh = self.backend.load(include_root=self.backend.root_loaded)
                candidates = [item for item in fresh if item.source == source and item.source_type == source_type and item.schedule == schedule and item.command == managed]
                new_entry = candidates[-1] if candidates else None
                if entry:
                    self.metadata_store.remove(entry)
                if new_entry:
                    tags = [item.strip() for item in tags_var.get().split(",") if item.strip()]
                    self.metadata_store.update(new_entry, entry_id=entry_id, name=name_var.get().strip(), tags=tags, favorite=favorite_var.get(), history_enabled=history_var.get(), prevent_overlap=lock_var.get(), timezone=timezone_var.get().strip(), original_command=command if managed != command else "")
                win.destroy(); self.refresh(); self._set_status(self.tr("changes_saved"), temporary=True)
            except (BackendError, ValueError, StopIteration) as exc:
                messagebox.showerror(self.tr("error"), str(exc), parent=win)
        ttk.Button(action_row, text=self.tr("save"), command=save, style="Accent.TButton").pack(side="right")
        win.bind("<Control-Return>", lambda _e: save())
        self._attach_resize_grip(win)

    def _systemd_editor(self, entry: CronEntry | None) -> None:
        if entry and not bool(entry.metadata.get("managed", False)):
            messagebox.showinfo(self.tr("systemd_timer"), self.tr("external_timer_notice"), parent=self.root)
            return
        win = tk.Toplevel(self.root); win.title(self.tr("systemd_timer")); win.geometry("760x520"); win.minsize(650, 440); win.resizable(True, True)
        frame = ttk.Frame(win, padding=14); frame.pack(fill="both", expand=True); frame.columnconfigure(1, weight=1)
        default_name = (entry.source[:-6].replace("prazycron-", "") if entry else self.tr("new_task_name"))
        name_var = tk.StringVar(value=default_name)
        calendar_var = tk.StringVar(value=entry.schedule if entry else "daily")
        desc_var = tk.StringVar(value=str(entry.metadata.get("description", self.tr("managed_task_desc"))) if entry else self.tr("managed_task_desc"))
        scope_var = tk.StringVar(value=self.tr("user_scope") if not entry or entry.source_type == "systemd_user_timer" else self.tr("system_scope"))
        persistent_var = tk.BooleanVar(value=True)
        random_var = tk.StringVar(value="")
        rows = [(self.tr("unit_name"), ttk.Entry(frame, textvariable=name_var)), (self.tr("on_calendar"), ttk.Entry(frame, textvariable=calendar_var)), (self.tr("description"), ttk.Entry(frame, textvariable=desc_var)), (self.tr("scope"), ttk.Combobox(frame, textvariable=scope_var, values=[self.tr("user_scope"), self.tr("system_scope")], state="readonly")), (self.tr("random_delay"), ttk.Entry(frame, textvariable=random_var))]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=6, padx=(0, 12)); widget.grid(row=i, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(frame, text=self.tr("persistent_help"), variable=persistent_var).grid(row=5, column=1, sticky="w", pady=6)
        ttk.Label(frame, text=self.tr("command_label")).grid(row=6, column=0, sticky="nw", pady=6)
        command = tk.Text(frame, height=8, wrap="word", bg=self._colors()["field"], fg=self._colors()["text"], insertbackground=self._colors()["text"]); command.grid(row=6, column=1, sticky="nsew", pady=6); frame.rowconfigure(6, weight=1)
        command.insert("1.0", entry.command if entry else "")
        buttons = ttk.Frame(frame); buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text=self.tr("cancel"), command=win.destroy).pack(side="right", padx=(6, 0))
        def save_timer() -> None:
            try:
                if entry:
                    delete_timer(entry)
                create_timer(name_var.get(), calendar_var.get(), command.get("1.0", "end-1c").strip(), desc_var.get(), user_scope=scope_var.get() == self.tr("user_scope"), persistent=persistent_var.get(), random_delay=random_var.get())
                win.destroy(); self.refresh()
            except Exception as exc:
                messagebox.showerror(self.tr("error"), str(exc), parent=win)
        ttk.Button(buttons, text=self.tr("save"), command=save_timer, style="Accent.TButton").pack(side="right")
        self._attach_resize_grip(win)

    def toggle_selected(self) -> None:
        entry = self._require_selection()
        if not entry or not self._authorize_change(): return
        try:
            if entry.source_type.startswith("systemd_"):
                toggle_timer(entry)
            else:
                self.backend.toggle_entry(entry)
            self.refresh(); self._set_status(self.tr("changes_saved"), temporary=True)
        except Exception as exc: self._show_error(exc)

    def duplicate_selected(self) -> None:
        entry = self._require_selection()
        if not entry or not self._authorize_change(): return
        if entry.source_type.startswith("systemd_"):
            self._systemd_editor(None); return
        try:
            self.backend.duplicate_entry(entry); self.refresh(); self._set_status(self.tr("changes_saved"), temporary=True)
        except Exception as exc: self._show_error(exc)

    def delete_selected(self) -> None:
        entry = self._require_selection()
        if not entry or not self._authorize_change(): return
        if self.cfg.get("confirm_destructive", True) and not messagebox.askyesno(self.tr("delete"), self.tr("confirm_delete"), parent=self.root): return
        try:
            if entry.source_type.startswith("systemd_"):
                delete_timer(entry)
            else:
                diff = self.backend.preview_delete(entry)
                if not self._confirm_diff(diff, parent=self.root): return
                self.backend.delete_entry(entry)
            self.metadata_store.remove(entry); self.refresh(); self._set_status(self.tr("changes_saved"), temporary=True)
        except Exception as exc: self._show_error(exc)

    def preview_selected(self) -> None:
        entry = self._require_selection()
        if not entry: return
        try:
            text = entry.raw if entry.source_type.startswith("systemd_") else self.backend.source_text(entry)
        except Exception as exc:
            self._show_error(exc); return
        win = tk.Toplevel(self.root)
        win.title(f"{self.tr('source_preview')} — {entry.source}")
        win.geometry("1000x720")
        win.minsize(650, 420)
        win.resizable(True, True)
        frame = ttk.Frame(win, padding=8)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1); frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=entry.source, style="Accent.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        text_widget = tk.Text(frame, wrap="none", undo=False, bg=self._colors()["field"], fg=self._colors()["text"],
                              insertbackground=self._colors()["text"], selectbackground=self._colors()["selected"])
        y = ttk.Scrollbar(frame, orient="vertical", style="Visible.Vertical.TScrollbar", command=text_widget.yview)
        x = ttk.Scrollbar(frame, orient="horizontal", style="Visible.Horizontal.TScrollbar", command=text_widget.xview)
        text_widget.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        text_widget.grid(row=1, column=0, sticky="nsew"); y.grid(row=1, column=1, sticky="ns"); x.grid(row=2, column=0, sticky="ew")
        text_widget.insert("1.0", text); text_widget.configure(state="disabled")
        if entry.line_index is not None:
            line = entry.line_index + 1
            text_widget.configure(state="normal"); text_widget.tag_add("current", f"{line}.0", f"{line}.end")
            text_widget.tag_configure("current", background=self._colors()["selected"], foreground="#ffffff")
            text_widget.see(f"{line}.0"); text_widget.configure(state="disabled")
        buttons = ttk.Frame(frame); buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text=self.tr("copy_all"), command=lambda: self._copy_text(text)).pack(side="left")
        if entry.source_type not in {"user_crontab", "root_crontab"} and not entry.source_type.startswith("systemd_"):
            ttk.Button(buttons, text=self.tr("open_editor"), command=lambda: self._open_path(entry.source)).pack(side="left", padx=6)
        ttk.Button(buttons, text=self.tr("close"), command=win.destroy).pack(side="right")
        win.bind("<F11>", lambda _e: self._toggle_maximize(win))
        self._attach_resize_grip(win)

    def copy_selected_command(self) -> None:
        entry = self._require_selection()
        if entry: self._copy_text(self._display_command(entry))

    def _copy_text(self, text: str) -> None:
        self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update_idletasks()

    def _open_path(self, path: str) -> None:
        try: subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc: self._show_error(exc)

    def explain_selected(self) -> None:
        entry = self._require_selection()
        if not entry:
            return
        self._show_analysis_panel()
        entry = replace(entry, command=self._display_command(entry))
        provider = self._provider_display_to_key.get(self.provider_var.get(), str(self.cfg.get("provider", "builtin")))
        if provider == "openai" and not get_api_key():
            key = simpledialog.askstring(self.tr("api_prompt_title"), self.tr("api_prompt"), show="*", parent=self.root)
            if not key:
                return
            set_session_api_key(key)
        if provider == "builtin":
            # Render now and again after Tk maps the pane, so the first click always shows it.
            self._show_builtin_analysis(entry)
            self.root.after_idle(lambda e=entry: self._show_builtin_analysis(e) if self.analysis_visible else None)
            self._set_status(self.tr("analysis_ready"), temporary=True)
            return
        self.analysis_task_var.set(self.tr("analysis_in_progress"))
        self.analysis_meta_var.set(f"{self.tr('provider')} {self.provider_var.get()}")
        for label in self.analysis_section_labels:
            label.configure(text=self.tr("analysis_in_progress"))
        def worker() -> None:
            try:
                result = ai_explain(entry, self.cfg, provider)
            except AIError as exc:
                result = str(exc)
            self.root.after(0, lambda: self._render_external_analysis(entry, result) if self.root.winfo_exists() else None)
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _replace_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal"); widget.delete("1.0", "end"); widget.insert("1.0", text); widget.configure(state="disabled")

    def load_root(self) -> None:
        self._set_status(self.tr("load_root") + "…")
        try:
            self.backend.read_root_crontab(authenticate=True)
            self.refresh()
        except Exception as exc: self._show_error(exc)

    def start_service(self) -> None:
        try:
            self.backend.start_service(); self._set_status(self.tr("service_started"), temporary=True); self.refresh()
        except Exception as exc: self._show_error(exc)

    def open_backups(self) -> None:
        win = tk.Toplevel(self.root); win.title(self.tr("backups")); win.geometry("1000x580"); win.minsize(700, 400); win.resizable(True, True)
        frame = ttk.Frame(win, padding=8); frame.pack(fill="both", expand=True); frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=("created", "source", "type", "size"), show="headings")
        for col, width in (("created",180),("source",520),("type",150),("size",100)):
            tree.heading(col, text=col.title()); tree.column(col, width=width, anchor="w")
        records = self.backend.list_backups(); record_by_id: dict[str, dict[str, object]] = {}
        for index, record in enumerate(records):
            iid = str(index); record_by_id[iid] = record
            tree.insert("", "end", iid=iid, values=(record.get("created"), record.get("source"), record.get("source_type"), record.get("size")))
        y = ttk.Scrollbar(frame, orient="vertical", style="Visible.Vertical.TScrollbar", command=tree.yview); tree.configure(yscrollcommand=y.set)
        tree.grid(row=0, column=0, sticky="nsew"); y.grid(row=0, column=1, sticky="ns")
        buttons = ttk.Frame(frame); buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8,0))
        def restore() -> None:
            selected = tree.selection()
            if not selected: return
            if not messagebox.askyesno(self.tr("restore"), self.tr("confirm_restore"), parent=win): return
            try:
                self.backend.restore_backup(record_by_id[selected[0]]); self.refresh(); messagebox.showinfo(self.tr("information"), self.tr("changes_saved"), parent=win)
            except Exception as exc: messagebox.showerror(self.tr("error"), str(exc), parent=win)
        ttk.Button(buttons, text=self.tr("restore"), command=restore, style="Accent.TButton").pack(side="left")
        ttk.Button(buttons, text=self.tr("backup_folder"), command=self.open_backup_folder).pack(side="left", padx=6)
        ttk.Button(buttons, text=self.tr("close"), command=win.destroy).pack(side="right")
        win.bind("<F11>", lambda _e: self._toggle_maximize(win))
        self._attach_resize_grip(win)

    def open_backup_folder(self) -> None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True); self._open_path(str(BACKUP_DIR))

    def open_settings(self) -> None:
        win = tk.Toplevel(self.root)
        win.title(self.tr("settings"))
        win.geometry("1120x800")
        win.minsize(900, 650)
        win.resizable(True, True)
        local = {k: (dict(v) if isinstance(v, dict) else v) for k, v in self.cfg.items()}
        local.setdefault("colors", dict(DEFAULTS["colors"]))

        container = ttk.Frame(win, padding=10)
        container.pack(fill="both", expand=True)
        container.rowconfigure(1, weight=1)
        container.columnconfigure(0, weight=1)

        top = ttk.Frame(container)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(top, text=self.tr("max_hint"), style="Muted.TLabel").pack(side="left")
        ttk.Button(top, text="⛶", width=4, command=lambda: self._toggle_maximize(win), style="Toolbar.TButton").pack(side="right")

        notebook = ttk.Notebook(container, style="Strong.TNotebook")
        notebook.grid(row=1, column=0, sticky="nsew")
        appearance = ttk.Frame(notebook, padding=14)
        online = ttk.Frame(notebook, padding=14)
        notebook.add(appearance, text=self.tr("appearance"))
        notebook.add(online, text=self.tr("online_ai"))

        language_var = tk.StringVar(value=LANGUAGES.get(str(local.get("language", "en")), "Polski"))
        theme_display_to_code = {self.tr(label_key): code for code, label_key in THEME_I18N_KEYS.items()}
        theme_var = tk.StringVar(value=self.tr(THEME_I18N_KEYS.get(str(local.get("theme", "dark")), "theme_dark")))
        font_var = tk.StringVar(value=str(local.get("font_family", "Noto Sans")))
        size_var = tk.IntVar(value=int(local.get("font_size", 11)))
        row_var = tk.IntVar(value=max(32, int(local.get("row_height", 40))))

        appearance.columnconfigure(0, weight=6, minsize=650)
        appearance.columnconfigure(1, weight=4, minsize=390)
        appearance.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(appearance, text=self.tr("general"), padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.columnconfigure(1, weight=1, minsize=245)
        right = ttk.LabelFrame(appearance, text=self.tr("row_colors"), padding=14)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        right.columnconfigure(1, weight=1)

        def clamp_int(variable: tk.IntVar, minimum: int, maximum: int, fallback: int) -> int:
            try:
                return max(minimum, min(maximum, int(variable.get())))
            except (tk.TclError, ValueError, TypeError):
                variable.set(fallback)
                return fallback

        def make_stepper(parent: ttk.Frame, variable: tk.IntVar, minimum: int, maximum: int, width: int = 6) -> ttk.Frame:
            holder = ttk.Frame(parent)

            def adjust(delta: int) -> None:
                current = clamp_int(variable, minimum, maximum, minimum)
                variable.set(max(minimum, min(maximum, current + delta)))

            holder.columnconfigure(1, weight=1)
            minus = ttk.Button(holder, text="−", width=3, command=lambda: adjust(-1), style="Stepper.TButton")
            minus.grid(row=0, column=0, sticky="nsew")
            entry = ttk.Entry(holder, textvariable=variable, width=width, justify="center")
            entry.grid(row=0, column=1, sticky="nsew", padx=7)
            plus = ttk.Button(holder, text="+", width=3, command=lambda: adjust(1), style="Stepper.TButton")
            plus.grid(row=0, column=2, sticky="nsew")
            return holder

        language_combo = ttk.Combobox(left, textvariable=language_var, values=list(LANGUAGES.values()), state="readonly")
        theme_combo = ttk.Combobox(left, textvariable=theme_var, values=list(theme_display_to_code), state="readonly")
        font_combo = ttk.Combobox(left, textvariable=font_var, values=("Noto Sans", "DejaVu Sans", "Ubuntu", "Liberation Sans", "Monospace"))
        size_stepper = make_stepper(left, size_var, 8, 24)
        row_stepper = make_stepper(left, row_var, 32, 120)
        general_rows = [
            (self.tr("language"), language_combo),
            (self.tr("theme"), theme_combo),
            (self.tr("font"), font_combo),
            (self.tr("font_size"), size_stepper),
            (self.tr("row_height"), row_stepper),
        ]
        for index, (label, widget) in enumerate(general_rows):
            ttk.Label(left, text=label).grid(row=index, column=0, sticky="w", padx=(0, 14), pady=7)
            widget.grid(row=index, column=1, sticky="ew", pady=7)
        ttk.Label(left, text=self.tr("row_height_auto"), style="Muted.TLabel", wraplength=520, justify="left").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(2, 8)
        )

        preview = ttk.LabelFrame(left, text=self.tr("preview"), padding=8, height=225)
        preview.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        preview.grid_propagate(False)
        left.rowconfigure(6, weight=1, minsize=225)
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)
        preview_style = "SettingsPreview.Treeview"
        preview_heading_style = "SettingsPreview.Treeview.Heading"
        preview_tree = ttk.Treeview(
            preview, columns=("schedule", "user", "source", "command"),
            show="tree headings", height=2, style=preview_style,
        )
        preview_tree.column("#0", width=100, stretch=False, anchor="center")
        for col, width in (("schedule", 240), ("user", 105), ("source", 135), ("command", 260)):
            preview_tree.column(col, width=width, anchor="w")
        preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(preview, orient="horizontal", style="Visible.Horizontal.TScrollbar", command=preview_tree.xview)
        preview_tree.configure(xscrollcommand=preview_scroll.set)
        preview_scroll.grid(row=1, column=0, sticky="ew")
        preview_font = tkfont.Font(root=win)
        preview_bold_font = tkfont.Font(root=win)

        color_keys = [
            ("row_even", self.tr("even_row")),
            ("row_odd", self.tr("odd_row")),
            ("selected", self.tr("selected_row")),
            ("text", self.tr("text_color")),
            ("muted", self.tr("muted_color")),
            ("accent", self.tr("accent_color")),
            ("grid", self.tr("grid_color")),
        ]
        swatches: dict[str, tk.Label] = {}

        def selected_language_code() -> str:
            return next((code for code, name in LANGUAGES.items() if name == language_var.get()), "en")

        def selected_theme_code() -> str:
            return theme_display_to_code.get(theme_var.get(), "dark")

        def update_preview(*_args: object) -> None:
            preview_tr = Translator(selected_language_code())
            colors = dict(local.get("colors", DEFAULTS["colors"]))
            family = font_var.get().strip() or "Noto Sans"
            font_size = clamp_int(size_var, 8, 24, 11)
            minimum_row_height = clamp_int(row_var, 32, 120, 40)
            preview_font.configure(family=family, size=font_size, weight="normal")
            preview_bold_font.configure(family=family, size=font_size, weight="bold")
            badge_height = max(
                self.status_on_image.height() if self.status_on_image is not None else 0,
                self.status_off_image.height() if self.status_off_image is not None else 0,
            )
            row_height = calculate_tree_row_height(
                minimum_row_height, int(preview_font.metrics("linespace")), badge_height,
            )
            style = ttk.Style(win)
            style.configure(
                preview_style,
                background=colors["row_even"], fieldbackground=colors["row_even"],
                foreground=colors["text"], rowheight=row_height,
                borderwidth=1, relief="solid", font=preview_font,
            )
            style.configure(
                preview_heading_style,
                background=colors["header"], foreground=colors["text"],
                padding=(10, 8), relief="raised", borderwidth=1, font=preview_bold_font,
            )
            style.map(preview_style, background=[("selected", colors["selected"])], foreground=[("selected", "#ffffff")])
            style.map(preview_heading_style, background=[("active", colors["accent"])])
            preview.configure(text=preview_tr("preview"))
            preview_tree.heading("#0", text=preview_tr("state"))
            for col in ("schedule", "user", "source", "command"):
                preview_tree.heading(col, text=preview_tr(col))
            preview_tree.tag_configure("even", background=colors["row_even"], foreground=colors["text"])
            preview_tree.tag_configure("odd", background=colors["row_odd"], foreground=colors["text"])
            preview_tree.delete(*preview_tree.get_children())
            use_pills = self.status_on_image is not None and self.status_off_image is not None
            preview_tree.insert(
                "", "end", text="" if use_pills else "ON",
                image=self.status_on_image if use_pills else "",
                values=(humanize_schedule("30 7-23 * * *", selected_language_code()), "root", "crontab", "/usr/local/bin/example.sh"), tags=("even",),
            )
            preview_tree.insert(
                "", "end", text="" if use_pills else "OFF",
                image=self.status_off_image if use_pills else "",
                values=(humanize_schedule("0 3 * * 0", selected_language_code()), "root", "cron.weekly", "/usr/local/bin/weekly-cleanup.sh"), tags=("odd",),
            )
            for key, swatch in swatches.items():
                swatch.configure(bg=colors.get(key, "#ffffff"))

        def choose_color(key: str) -> None:
            initial = local["colors"].get(key, "#ffffff")
            chosen = colorchooser.askcolor(initialcolor=initial, parent=win)[1]
            if chosen:
                local["colors"][key] = chosen
                update_preview()

        for index, (key, label) in enumerate(color_keys):
            ttk.Label(right, text=label).grid(row=index, column=0, sticky="w", padx=(0, 12), pady=6)
            holder = ttk.Frame(right)
            holder.grid(row=index, column=1, sticky="e", pady=6)
            swatch = tk.Label(holder, width=9, height=1, bg=local["colors"].get(key, "#fff"), relief="solid", borderwidth=1)
            swatch.pack(side="left")
            swatches[key] = swatch
            ttk.Button(holder, text="…", width=3, command=lambda k=key: choose_color(k)).pack(side="left", padx=(7, 0))

        tools_row = len(color_keys) + 1
        ttk.Separator(right, orient="horizontal").grid(row=tools_row, column=0, columnspan=2, sticky="ew", pady=(14, 10))
        ttk.Button(right, text=self.tr("visible_columns"), command=self.configure_columns).grid(
            row=tools_row + 1, column=0, columnspan=2, sticky="ew", pady=5,
        )
        ttk.Button(right, text=self.tr("readonly_password"), command=self.open_security_settings).grid(
            row=tools_row + 2, column=0, columnspan=2, sticky="ew", pady=5,
        )

        provider_var = tk.StringVar(value=self.tr(str(local.get("provider", "builtin"))))
        endpoint_var = tk.StringVar(value=str(local.get("openai_endpoint")))
        model_var = tk.StringVar(value=str(local.get("openai_model")))
        ollama_endpoint_var = tk.StringVar(value=str(local.get("ollama_endpoint")))
        ollama_model_var = tk.StringVar(value=str(local.get("ollama_model")))
        api_var = tk.StringVar(value="")
        provider_map = {self.tr(key): key for key in PROVIDER_KEYS}
        online.columnconfigure(1, weight=1)
        ttk.Label(online, text=self.tr("provider")).grid(row=0, column=0, sticky="w", padx=(0, 14), pady=8)
        settings_provider_combo = ttk.Combobox(online, textvariable=provider_var, values=list(provider_map), state="readonly")
        settings_provider_combo.grid(row=0, column=1, sticky="ew", pady=8)
        provider_options = ttk.LabelFrame(online, text=self.tr("online_ai"), padding=14)
        provider_options.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        provider_options.columnconfigure(1, weight=1)
        online.rowconfigure(1, weight=1)

        def render_provider_options(_event: object = None) -> None:
            for child in provider_options.winfo_children():
                child.destroy()
            selected = provider_map.get(provider_var.get(), "builtin")
            if selected == "builtin":
                ttk.Label(provider_options, text=self.tr("builtin_no_key"), style="Muted.TLabel", wraplength=700, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=8)
                return
            if selected == "ollama":
                rows = [
                    ("Ollama " + self.tr("endpoint"), ttk.Entry(provider_options, textvariable=ollama_endpoint_var)),
                    ("Ollama " + self.tr("model"), ttk.Entry(provider_options, textvariable=ollama_model_var)),
                ]
                note = self.tr("ollama_no_key")
            else:
                rows = [
                    ("OpenAI " + self.tr("endpoint"), ttk.Entry(provider_options, textvariable=endpoint_var)),
                    ("OpenAI " + self.tr("model"), ttk.Entry(provider_options, textvariable=model_var)),
                    (self.tr("api_key"), ttk.Entry(provider_options, textvariable=api_var, show="*")),
                ]
                note = "ⓘ " + self.tr("api_note") + " " + self.tr("session_only")
            for index, (label, widget) in enumerate(rows):
                ttk.Label(provider_options, text=label).grid(row=index, column=0, sticky="w", padx=(0, 14), pady=8)
                widget.grid(row=index, column=1, sticky="ew", pady=8)
            ttk.Label(provider_options, text=note, style="Muted.TLabel", wraplength=700, justify="left").grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(14, 4))

        settings_provider_combo.bind("<<ComboboxSelected>>", render_provider_options)
        render_provider_options()

        def theme_changed(*_args: object) -> None:
            theme_code = selected_theme_code()
            local["theme"] = theme_code
            local["colors"] = dict(THEMES[theme_code])
            update_preview()

        language_var.trace_add("write", update_preview)
        font_var.trace_add("write", update_preview)
        size_var.trace_add("write", update_preview)
        row_var.trace_add("write", update_preview)
        theme_var.trace_add("write", theme_changed)
        update_preview()

        buttons = ttk.Frame(container)
        buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0), padx=(0, 30))

        def reset_defaults() -> None:
            local["colors"] = dict(DEFAULTS["colors"])
            local["theme"] = str(DEFAULTS["theme"])
            language_var.set(LANGUAGES.get(str(DEFAULTS["language"]), "English"))
            theme_var.set(self.tr(THEME_I18N_KEYS.get(str(DEFAULTS["theme"]), "theme_dark")))
            font_var.set(str(DEFAULTS["font_family"]))
            size_var.set(int(DEFAULTS["font_size"]))
            row_var.set(int(DEFAULTS["row_height"]))
            update_preview()

        ttk.Button(buttons, text=self.tr("reset"), command=reset_defaults).pack(side="left")
        ttk.Button(buttons, text=self.tr("cancel"), command=win.destroy).pack(side="right", padx=(6, 0))

        def apply(close: bool = False) -> None:
            lang_code = selected_language_code()
            theme_code = selected_theme_code()
            local.update({
                "language": lang_code,
                "language_explicit": True,
                "theme": theme_code,
                "font_family": font_var.get().strip() or "Noto Sans",
                "font_size": clamp_int(size_var, 8, 24, 11),
                "row_height": clamp_int(row_var, 32, 120, 40),
                "provider": provider_map.get(provider_var.get(), "builtin"),
                "openai_endpoint": endpoint_var.get().strip(),
                "openai_model": model_var.get().strip(),
                "ollama_endpoint": ollama_endpoint_var.get().strip(),
                "ollama_model": ollama_model_var.get().strip(),
            })
            # Alternating row colors and rounded ON/OFF badges are permanent UI behavior.
            local.pop("alternate_rows", None)
            local.pop("status_pills", None)
            if api_var.get().strip():
                set_session_api_key(api_var.get().strip())
            self.cfg = local
            save_config(self.cfg)
            self.tr.set_language(lang_code)
            if close:
                win.destroy()
            self._build_ui()
            self.refresh()

        ttk.Button(buttons, text=self.tr("apply"), command=lambda: apply(False)).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text=self.tr("ok"), command=lambda: apply(True), style="Accent.TButton").pack(side="right")
        win.bind("<F11>", lambda _e: self._toggle_maximize(win))
        self._attach_resize_grip(win)

    def _authorize_change(self) -> bool:
        if bool(self.cfg.get("read_only", False)):
            messagebox.showwarning(self.tr("readonly_mode"), self.tr("readonly_warning"), parent=self.root)
            return False
        record = self.cfg.get("password_record")
        if record and not self._session_unlocked:
            password = simpledialog.askstring(self.tr("unlock_changes"), self.tr("lock_password_prompt"), show="*", parent=self.root)
            if password is None or not verify_password(password, record if isinstance(record, dict) else None):
                messagebox.showerror(self.tr("error"), self.tr("unlock_failed"), parent=self.root)
                return False
            self._session_unlocked = True
        return True

    def _show_text_window(self, title: str, text: str, *, parent: tk.Misc | None = None, width: int = 980, height: int = 650) -> tk.Toplevel:
        win = tk.Toplevel(parent or self.root); win.title(title); win.geometry(f"{width}x{height}"); win.minsize(620, 400); win.resizable(True, True)
        frame = ttk.Frame(win, padding=10); frame.pack(fill="both", expand=True); frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        widget = tk.Text(frame, wrap="word", bg=self._colors()["field"], fg=self._colors()["text"], insertbackground=self._colors()["text"], selectbackground=self._colors()["selected"], padx=12, pady=10)
        scroll = ttk.Scrollbar(frame, orient="vertical", style="Visible.Vertical.TScrollbar", command=widget.yview); widget.configure(yscrollcommand=scroll.set)
        widget.grid(row=0, column=0, sticky="nsew"); scroll.grid(row=0, column=1, sticky="ns")
        widget.insert("1.0", text); widget.configure(state="disabled")
        buttons = ttk.Frame(frame); buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8,0))
        ttk.Button(buttons, text=self.tr("copy_all"), command=lambda: self._copy_text(text)).pack(side="left")
        ttk.Button(buttons, text=self.tr("close"), command=win.destroy).pack(side="right")
        win.bind("<F11>", lambda _e: self._toggle_maximize(win)); self._attach_resize_grip(win)
        return win

    def _show_validation_issues(self, issues: list[ValidationIssue], *, parent: tk.Misc | None = None) -> None:
        if not issues:
            messagebox.showinfo(self.tr("validation"), self.tr("validation_no_issues"), parent=parent or self.root); return
        labels = {"error": self.tr("severity_error"), "warning": self.tr("severity_warning"), "info": self.tr("severity_info")}
        text = "\n".join(f"[{labels.get(issue.severity, issue.severity.upper())}] {issue.message}" for issue in issues)
        self._show_text_window(self.tr("validation_task"), text, parent=parent, width=850, height=520)

    def _confirm_warnings(self, issues: list[ValidationIssue], *, parent: tk.Misc | None = None) -> bool:
        warnings = [issue.message for issue in issues if issue.severity == "warning"]
        if not warnings:
            return True
        return messagebox.askyesno(self.tr("validation_warnings"), self.tr("validation_warning_prompt", warnings="\n• ".join(warnings)), parent=parent or self.root)

    def _confirm_diff(self, diff: str, *, parent: tk.Misc | None = None) -> bool:
        win = tk.Toplevel(parent or self.root); win.title(self.tr("diff_title")); win.geometry("1000x680"); win.minsize(680, 440); win.resizable(True, True)
        frame = ttk.Frame(win, padding=10); frame.pack(fill="both", expand=True); frame.rowconfigure(1, weight=1); frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=self.tr("diff_backup_notice"), style="Accent.TLabel").grid(row=0, column=0, sticky="w", pady=(0,8))
        widget = tk.Text(frame, wrap="none", bg=self._colors()["field"], fg=self._colors()["text"], insertbackground=self._colors()["text"])
        y=ttk.Scrollbar(frame, orient="vertical", style="Visible.Vertical.TScrollbar", command=widget.yview); x=ttk.Scrollbar(frame, orient="horizontal", style="Visible.Horizontal.TScrollbar", command=widget.xview); widget.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        widget.grid(row=1,column=0,sticky="nsew"); y.grid(row=1,column=1,sticky="ns"); x.grid(row=2,column=0,sticky="ew")
        widget.insert("1.0", diff); widget.tag_configure("add", foreground="#70d578"); widget.tag_configure("del", foreground="#ff7676")
        for line_no, line in enumerate(diff.splitlines(), 1):
            if line.startswith("+") and not line.startswith("+++"): widget.tag_add("add", f"{line_no}.0", f"{line_no}.end")
            elif line.startswith("-") and not line.startswith("---"): widget.tag_add("del", f"{line_no}.0", f"{line_no}.end")
        widget.configure(state="disabled")
        result={"ok":False}; buttons=ttk.Frame(frame); buttons.grid(row=3,column=0,columnspan=2,sticky="e",pady=(8,0))
        def accept() -> None: result["ok"]=True; win.destroy()
        ttk.Button(buttons,text=self.tr("cancel"),command=win.destroy).pack(side="right",padx=(6,0)); ttk.Button(buttons,text=self.tr("approve_save"),command=accept,style="Accent.TButton").pack(side="right")
        win.transient(parent or self.root); win.grab_set(); self._attach_resize_grip(win); (parent or self.root).wait_window(win)
        return bool(result["ok"])

    def validate_selected(self) -> None:
        entry = self._require_selection()
        if not entry: return
        if entry.source_type.startswith("systemd_"):
            messagebox.showinfo(self.tr("validation"), self.tr("timer_validation_info"), parent=self.root); return
        self._show_validation_issues(validate_entry(entry.schedule, self._display_command(entry), entry.user, source_type=entry.source_type))

    def _show_next_runs_for(self, schedule: str, timezone_name: str, *, parent: tk.Misc | None = None) -> None:
        try:
            runs = next_runs(schedule, 10, timezone_name=timezone_name)
            if not runs:
                text = self.tr("reboot_no_dates")
            else:
                text = self.tr("timezone_label", zone=timezone_name) + "\n\n" + "\n".join(f"{i:>2}. {run.strftime('%A, %Y-%m-%d %H:%M:%S %Z')}" for i, run in enumerate(runs, 1))
            note = dst_note(schedule, timezone_name)
            if note: text += "\n\n⚠ " + note
            self._show_text_window(self.tr("next_runs_title"), text, parent=parent, width=760, height=560)
        except ValueError as exc:
            messagebox.showerror(self.tr("invalid_schedule"), str(exc), parent=parent or self.root)

    def show_next_runs(self) -> None:
        entry = self._require_selection()
        if not entry: return
        if entry.source_type.startswith("systemd_"):
            self._show_text_window(self.tr("systemd_timer"), f"{self.tr('timer_unit', unit=entry.source)}\n{self.tr('timer_schedule', schedule=entry.schedule)}\n\n{self.tr('command_label')} {entry.command}"); return
        timezone_name = str(self._entry_metadata(entry).get("timezone") or system_timezone_name())
        self._show_next_runs_for(entry.schedule, timezone_name)

    def run_selected_now(self) -> None:
        entry = self._require_selection()
        if not entry: return
        if entry.source_type.startswith("systemd_"):
            unit = str(entry.metadata.get("service_unit", entry.source[:-6] + ".service")); user_scope = entry.source_type == "systemd_user_timer"
            cmd = (["systemctl", "--user", "start", unit] if user_scope else ["pkexec", "systemctl", "start", unit])
            if not messagebox.askyesno(self.tr("run_now"), self.tr("run_timer_confirm", unit=unit), parent=self.root): return
            def timer_worker() -> None:
                proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
                self.root.after(0, lambda: self._show_text_window(self.tr("run_timer_result"), (proc.stdout or "") + (proc.stderr or "") + f"\n{self.tr('exit_code')}: {proc.returncode}"))
            threading.Thread(target=timer_worker, daemon=True).start(); return
        command = self._display_command(entry)
        if self.cfg.get("confirm_run_now", True) and not messagebox.askyesno(self.tr("run_now"), self.tr("run_task_confirm", user=entry.user, source=entry.source, command=command), parent=self.root): return
        self._set_status(self.tr("running_task"))
        def worker() -> None:
            try:
                clone = replace(entry, command=command)
                record = self.backend.run_now(clone, timeout=int(self.cfg.get("run_timeout", 3600)), entry_id=str(self._entry_metadata(entry).get("entry_id") or entry_signature(entry)))
                text = f"{self.tr('exit_code')}: {record.exit_code}\n{self.tr('duration')}: {record.duration_seconds:.3f} s\n{self.tr('start')}: {record.started}\n{self.tr('finish')}: {record.finished}\n\n{self.tr('stdout')}\n{'─'*50}\n{record.stdout or self.tr('none')}\n\n{self.tr('stderr')}\n{'─'*50}\n{record.stderr or self.tr('none')}"
                self.root.after(0, lambda: (self._show_text_window(self.tr("execution_result"), text), self.refresh()))
            except Exception as exc:
                self.root.after(0, lambda: self._show_error(exc))
        threading.Thread(target=worker, daemon=True).start()

    def show_history(self) -> None:
        entry = self._require_selection()
        if not entry: return
        meta = self._entry_metadata(entry); history_id = str(meta.get("entry_id") or entry_signature(entry)); records = read_history(history_id, limit=int(self.cfg.get("history_limit", 200)))
        if not records:
            self._show_text_window(self.tr("execution_history"), self.tr("no_saved_runs") + "\n\n" + self.tr("history_hint")); return
        parts=[]
        for rec in records:
            parts.append(f"{rec.finished}  {self.tr('history_ok') if rec.exit_code == 0 else self.tr('history_error')}  {self.tr('history_code')}={rec.exit_code}  {self.tr('duration')}={rec.duration_seconds:.3f}s  {self.tr('history_mode')}={rec.mode}\n{self.tr('command_label')} {rec.command}\n{self.tr('stdout')}: {(rec.stdout or self.tr('none')).strip()[:1000]}\n{self.tr('stderr')}: {(rec.stderr or self.tr('none')).strip()[:1000]}\n{'='*80}")
        self._show_text_window(self.tr("execution_history"), "\n".join(parts), width=1100, height=720)

    def edit_environment(self, entry: CronEntry | None = None) -> None:
        entry = entry or self._require_selection()
        if not entry: return
        if entry.source_type.startswith("systemd_") or entry.source_type == "directory_script":
            messagebox.showinfo(self.tr("environment"), self.tr("no_cron_environment"), parent=self.root); return
        if not self._authorize_change(): return
        try: original = self.backend.source_text(entry); current = parse_environment(original)
        except Exception as exc: self._show_error(exc); return
        win=tk.Toplevel(self.root); win.title(self.tr("environment_title", source=entry.source)); win.geometry("780x620"); win.minsize(650,500); win.resizable(True,True)
        frame=ttk.Frame(win,padding=12); frame.pack(fill="both",expand=True); frame.columnconfigure(1,weight=1)
        vars_by_name: dict[str,tk.StringVar]={}
        for row,name in enumerate(KNOWN_ENV):
            var=tk.StringVar(value=current.get(name,"")); vars_by_name[name]=var; ttk.Label(frame,text=name+":").grid(row=row,column=0,sticky="w",pady=5); ttk.Entry(frame,textvariable=var).grid(row=row,column=1,sticky="ew",pady=5)
        ttk.Label(frame,text=self.tr("additional_env")).grid(row=len(KNOWN_ENV),column=0,columnspan=2,sticky="w",pady=(12,5))
        extra=tk.Text(frame,height=10,wrap="none",bg=self._colors()["field"],fg=self._colors()["text"],insertbackground=self._colors()["text"]); extra.grid(row=len(KNOWN_ENV)+1,column=0,columnspan=2,sticky="nsew"); frame.rowconfigure(len(KNOWN_ENV)+1,weight=1)
        extra.insert("1.0","\n".join(f"{k}={v}" for k,v in current.items() if k not in KNOWN_ENV))
        buttons=ttk.Frame(frame); buttons.grid(row=len(KNOWN_ENV)+2,column=0,columnspan=2,sticky="e",pady=(10,0)); ttk.Button(buttons,text=self.tr("cancel"),command=win.destroy).pack(side="right",padx=(6,0))
        def save_env() -> None:
            values={name:var.get() for name,var in vars_by_name.items()}
            for line in extra.get("1.0","end-1c").splitlines():
                if not line.strip(): continue
                if "=" not in line: messagebox.showerror(self.tr("error"),self.tr("invalid_line", line=line),parent=win); return
                name,value=line.split("=",1); values[name.strip()]=value.strip()
            changed=update_environment(original,values); diff=self.backend.unified_diff(original,changed,entry.source)
            if not self._confirm_diff(diff,parent=win): return
            try:
                self.backend.update_source_environment(entry,values); win.destroy(); self.refresh()
            except Exception as exc: messagebox.showerror(self.tr("error"),str(exc),parent=win)
        ttk.Button(buttons,text=self.tr("save"),command=save_env,style="Accent.TButton").pack(side="right"); self._attach_resize_grip(win)

    def open_diagnostics(self) -> None:
        self._set_status(self.tr("running_diagnostics"))
        def worker() -> None:
            try:
                cron_entries=self.backend.load(include_root=self.backend.root_loaded); items=diagnose(self.backend,cron_entries)
                icons={"ok":"✓","info":"i","warning":"⚠","error":"✕"}; text="\n".join(f"{icons.get(item.severity,'•')} [{item.severity.upper()}] {item.subject}: {item.message}" for item in items)
                self.root.after(0,lambda:self._show_text_window(self.tr("diagnostics_cron"),text,width=1100,height=720))
            except Exception as exc: self.root.after(0,lambda:self._show_error(exc))
        threading.Thread(target=worker,daemon=True).start()

    def show_conflicts(self) -> None:
        entries=self.backend.load(include_root=self.backend.root_loaded); conflicts=detect_conflicts(entries)
        text=self.tr("no_conflicts") if not conflicts else "\n".join(f"⚠ {item.message}" for item in conflicts)
        self._show_text_window(self.tr("conflicts_duplicates"),text)

    def edit_metadata(self) -> None:
        entry=self._require_selection()
        if not entry:return
        meta=self._entry_metadata(entry); win=tk.Toplevel(self.root); win.title(self.tr("metadata_title")); win.geometry("620x320"); win.minsize(520,260); win.resizable(True,True)
        frame=ttk.Frame(win,padding=14); frame.pack(fill="both",expand=True); frame.columnconfigure(1,weight=1)
        name=tk.StringVar(value=str(meta.get("name",""))); tags=tk.StringVar(value=", ".join(meta.get("tags",[]) if isinstance(meta.get("tags"),list) else [])); favorite=tk.BooleanVar(value=bool(meta.get("favorite",False)))
        ttk.Label(frame,text=self.tr("name")).grid(row=0,column=0,sticky="w",pady=8); ttk.Entry(frame,textvariable=name).grid(row=0,column=1,sticky="ew",pady=8)
        ttk.Label(frame,text=self.tr("tags")).grid(row=1,column=0,sticky="w",pady=8); ttk.Entry(frame,textvariable=tags).grid(row=1,column=1,sticky="ew",pady=8)
        ttk.Checkbutton(frame,text=self.tr("favorite"),variable=favorite).grid(row=2,column=1,sticky="w",pady=8)
        buttons=ttk.Frame(frame); buttons.grid(row=3,column=0,columnspan=2,sticky="e",pady=(15,0)); ttk.Button(buttons,text=self.tr("cancel"),command=win.destroy).pack(side="right",padx=(6,0))
        def save_meta() -> None:
            self.metadata_store.update(entry,name=name.get().strip(),tags=[x.strip() for x in tags.get().split(',') if x.strip()],favorite=favorite.get()); win.destroy(); self.apply_filters()
        ttk.Button(buttons,text=self.tr("save"),command=save_meta,style="Accent.TButton").pack(side="right"); self._attach_resize_grip(win)

    def export_selected(self) -> None:
        entry=self._require_selection()
        if not entry:return
        meta=self._entry_metadata(entry); payload={"format":"PrazyCron task","version":2,"kind":"systemd" if entry.source_type.startswith("systemd_") else "cron","enabled":entry.enabled,"schedule":entry.schedule,"command":self._display_command(entry),"user":entry.user,"source":entry.source,"source_type":entry.source_type,"metadata":meta}
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".json",filetypes=[("PrazyCron JSON","*.json"),(self.tr("all_files"),"*")],initialfile=(str(meta.get('name') or 'prazycron-task').replace(' ','-')+'.json'))
        if path:
            Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")

    def import_task(self) -> None:
        if not self._authorize_change():return
        path=filedialog.askopenfilename(parent=self.root,filetypes=[("PrazyCron JSON","*.json"),(self.tr("cron_text"),"*.cron *.txt"),(self.tr("all_files"),"*")])
        if not path:return
        try:
            text=Path(path).read_text(encoding="utf-8",errors="replace")
            if path.lower().endswith('.json'):
                data=json.loads(text); kind=str(data.get('kind','cron'))
                if kind=='systemd':
                    create_timer(str(data.get('metadata',{}).get('name') or Path(path).stem),str(data['schedule']),str(data['command']),user_scope=True); self.view_mode='systemd'; self.refresh(); return
                schedule=str(data['schedule']); command=str(data['command']); user=str(data.get('user') or getpass.getuser()); enabled=bool(data.get('enabled',True)); metadata=data.get('metadata',{})
            else:
                from .cron import split_cron_line
                parsed=next((split_cron_line(line,False) for line in text.splitlines() if split_cron_line(line,False)),None)
                if not parsed: raise ValueError(self.tr('invalid_cron_import'))
                schedule,command,_=parsed; user=getpass.getuser(); enabled=True; metadata={}
            issues=validate_entry(schedule,command,user,source_type='user_crontab')
            if any(x.severity=='error' for x in issues): self._show_validation_issues(issues); return
            source='crontab:user'; source_type='user_crontab'; _b,_a,diff=self.backend.preview_add(source,source_type,schedule,command,user,enabled)
            if not self._confirm_diff(diff):return
            self.backend.add_entry(source,source_type,schedule,command,user,enabled); fresh=self.backend.load(); new=next((e for e in reversed(fresh) if e.source_type==source_type and e.schedule==schedule and e.command==command),None)
            if new and isinstance(metadata,dict): self.metadata_store.update(new,**metadata)
            self.view_mode='cron'; self.refresh()
        except Exception as exc:self._show_error(exc)

    def configure_columns(self) -> None:
        columns=[('state',self.tr('state')),('favorite',self.tr('favorite_column')),('name',self.tr('name_tags_column')),('schedule',self.tr('schedule')),('user',self.tr('user')),('source',self.tr('source').rstrip(':')),('last_run',self.tr('last_run')),('next_run',self.tr('next_run')),('command',self.tr('command'))]
        current=set(self.cfg.get('visible_columns',[key for key,_ in columns])); win=tk.Toplevel(self.root); win.title(self.tr('table_columns')); win.geometry('480x520'); win.resizable(True,True); frame=ttk.Frame(win,padding=14); frame.pack(fill='both',expand=True)
        variables={key:tk.BooleanVar(value=key in current) for key,_ in columns}
        for key,label in columns: ttk.Checkbutton(frame,text=label,variable=variables[key]).pack(anchor='w',pady=5)
        buttons=ttk.Frame(frame); buttons.pack(fill='x',pady=(14,0)); ttk.Button(buttons,text=self.tr('cancel'),command=win.destroy).pack(side='right',padx=(6,0))
        def apply_columns() -> None:
            selected=[key for key,_ in columns if variables[key].get()]
            if 'state' not in selected:selected.insert(0,'state')
            self.cfg['visible_columns']=selected; save_config(self.cfg); win.destroy(); self._build_ui(); self.refresh()
        ttk.Button(buttons,text=self.tr('save'),command=apply_columns,style='Accent.TButton').pack(side='right'); self._attach_resize_grip(win)

    def open_security_settings(self) -> None:
        record=self.cfg.get('password_record')
        if record and not self._session_unlocked:
            password=simpledialog.askstring(self.tr('security'),self.tr('current_password'),show='*',parent=self.root)
            if password is None or not verify_password(password,record if isinstance(record,dict) else None): messagebox.showerror(self.tr('error'),self.tr('wrong_password'),parent=self.root); return
            self._session_unlocked=True
        win=tk.Toplevel(self.root); win.title(self.tr('readonly_title')); win.geometry('650x390'); win.minsize(560,330); win.resizable(True,True); frame=ttk.Frame(win,padding=16); frame.pack(fill='both',expand=True); frame.columnconfigure(1,weight=1)
        read_only=tk.BooleanVar(value=bool(self.cfg.get('read_only',False))); password=tk.StringVar(); repeat=tk.StringVar(); clear=tk.BooleanVar(value=False)
        ttk.Checkbutton(frame,text=self.tr('readonly_all'),variable=read_only).grid(row=0,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Label(frame,text=self.tr('new_password')).grid(row=1,column=0,sticky='w',pady=8); ttk.Entry(frame,textvariable=password,show='*').grid(row=1,column=1,sticky='ew',pady=8)
        ttk.Label(frame,text=self.tr('repeat_password')).grid(row=2,column=0,sticky='w',pady=8); ttk.Entry(frame,textvariable=repeat,show='*').grid(row=2,column=1,sticky='ew',pady=8)
        ttk.Checkbutton(frame,text=self.tr('remove_password'),variable=clear).grid(row=3,column=1,sticky='w',pady=8)
        ttk.Label(frame,text=self.tr('security_note'),style='Muted.TLabel',wraplength=560,justify='left').grid(row=4,column=0,columnspan=2,sticky='ew',pady=12)
        buttons=ttk.Frame(frame); buttons.grid(row=5,column=0,columnspan=2,sticky='e'); ttk.Button(buttons,text=self.tr('cancel'),command=win.destroy).pack(side='right',padx=(6,0))
        def save_security() -> None:
            if password.get() and password.get()!=repeat.get(): messagebox.showerror(self.tr('error'),self.tr('passwords_differ'),parent=win); return
            self.cfg['read_only']=read_only.get()
            if clear.get(): self.cfg['password_record']=None; self._session_unlocked=False
            elif password.get(): self.cfg['password_record']=make_password_record(password.get()); self._session_unlocked=True
            save_config(self.cfg); win.destroy(); self._set_status(self.tr('security_saved'),temporary=True)
        ttk.Button(buttons,text=self.tr('save'),command=save_security,style='Accent.TButton').pack(side='right'); self._attach_resize_grip(win)

    def _attach_resize_grip(self, window: tk.Misc) -> None:
        """Add a large, visible bottom-right resize handle to normal windows."""
        old_grip = getattr(window, "_prazycron_resize_grip", None)
        if old_grip is not None:
            try:
                old_grip.destroy()
            except tk.TclError:
                pass
        old_bind = getattr(window, "_prazycron_resize_bind", None)
        if old_bind:
            try:
                window.unbind("<Configure>", old_bind)
            except tk.TclError:
                pass

        c = self._colors()
        try:
            grip = tk.Canvas(window, width=28, height=28, bg=c["panel"], highlightbackground=c["accent"],
                             highlightthickness=1, borderwidth=0, cursor="bottom_right_corner")
        except tk.TclError:
            grip = tk.Canvas(window, width=28, height=28, bg=c["panel"], highlightbackground=c["accent"],
                             highlightthickness=1, borderwidth=0, cursor="sizing")
        for offset in (6, 11, 16):
            grip.create_line(27 - offset, 27, 27, 27 - offset, fill=c["accent"], width=2)
        window._prazycron_resize_grip = grip  # type: ignore[attr-defined]
        drag: dict[str, int] = {}

        def is_maximized() -> bool:
            try:
                if str(window.state()) == "zoomed":
                    return True
            except (tk.TclError, AttributeError):
                pass
            try:
                return bool(window.attributes("-zoomed"))
            except (tk.TclError, AttributeError):
                return False

        def update_visibility(_event: object = None) -> None:
            if not window.winfo_exists():
                return
            if is_maximized():
                grip.place_forget()
            else:
                grip.place(relx=1.0, rely=1.0, anchor="se")
                grip.tk.call("raise", grip._w)

        def begin(event: tk.Event) -> None:
            drag.update(x=event.x_root, y=event.y_root, width=window.winfo_width(), height=window.winfo_height())

        def resize(event: tk.Event) -> str:
            min_width, min_height = window.minsize()
            width = max(int(min_width), drag.get("width", window.winfo_width()) + event.x_root - drag.get("x", event.x_root))
            height = max(int(min_height), drag.get("height", window.winfo_height()) + event.y_root - drag.get("y", event.y_root))
            try:
                window.geometry(f"{width}x{height}")
            except tk.TclError:
                pass
            return "break"

        grip.bind("<ButtonPress-1>", begin)
        grip.bind("<B1-Motion>", resize)
        bind_id = window.bind("<Configure>", update_visibility, add="+")
        window._prazycron_resize_bind = bind_id  # type: ignore[attr-defined]
        window.after_idle(update_visibility)

    @staticmethod
    def _toggle_maximize(window: tk.Toplevel) -> None:
        try:
            window.state("normal" if window.state() == "zoomed" else "zoomed")
        except tk.TclError:
            try: window.attributes("-zoomed", not bool(window.attributes("-zoomed")))
            except tk.TclError: pass

    def show_about(self) -> None:
        messagebox.showinfo(APP_FULL_NAME, f"{APP_FULL_NAME} {__version__}\n{APP_TAGLINE}\n\n{self.tr('about_text')}", parent=self.root)

    def _set_status(self, text: str, temporary: bool=False) -> None:
        self.status_var.set(text)
        if self._status_after_id:
            self.root.after_cancel(self._status_after_id); self._status_after_id=None
        if temporary:
            self._status_after_id = self.root.after(3500, self._update_status)

    def _update_status(self) -> None:
        active = sum(1 for e in self.entries if e.enabled)
        if self.view_mode == "systemd":
            self._set_status(self.tr("systemd_status", total=len(self.entries), active=active))
            return
        service = self.tr("service_running") if self.backend.service_running() else self.tr("service_stopped")
        root = self.tr("root_loaded") if self.backend.root_loaded else self.tr("root_not_loaded")
        self._set_status(self.tr("found", total=len(self.entries), active=active, service=service, root=root))

    def _show_error(self, exc: Exception) -> None:
        self._set_status(str(exc))
        messagebox.showerror(self.tr("error"), str(exc), parent=self.root)


def run_gui(smoke_test: bool = False) -> int:
    root = tk.Tk()
    PrazyCronGUI(root, smoke_test=smoke_test)
    root.mainloop()
    return 0
