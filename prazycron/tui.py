from __future__ import annotations

import curses
import getpass
import json
import os
import shlex
import subprocess
import textwrap
import uuid
from dataclasses import replace
from pathlib import Path

from . import APP_TAGLINE, __version__
from .ai import AIError, explain as ai_explain, get_api_key, set_session_api_key
from .analyzer import humanize_schedule
from .backend import BACKUP_DIR, BackendError, CronBackend
from .config import load_config, save_config
from .i18n import LANGUAGES, Translator
from .model import CronEntry
from .diagnostics import diagnose
from .environment import KNOWN_ENV, parse_environment, update_environment
from .execution import HISTORY_DIR, read_history
from .metadata import MetadataStore, entry_signature
from .overlap import detect_conflicts, unwrap_prazycron
from .schedule import dst_note, next_runs, system_timezone_name
from .security import verify_password
from .systemd_timers import create_timer, delete_timer, list_timers, toggle_timer
from .validation import validate_entry

TUI_PRESETS = [
    ("custom", ""),
    ("every_minute", "* * * * *"),
    ("hourly", "0 * * * *"),
    ("daily", "0 3 * * *"),
    ("weekly", "0 3 * * 0"),
    ("monthly", "0 3 1 * *"),
    ("at_reboot", "@reboot"),
]


class TUI:
    def __init__(self, stdscr: curses.window) -> None:
        self.stdscr = stdscr
        self.cfg = load_config()
        self.tr = Translator(str(self.cfg.get("language", "en")))
        self.backend = CronBackend()
        self.entries: list[CronEntry] = []
        self.filtered: list[CronEntry] = []
        self.index = 0
        self.offset = 0
        self.query = ""
        self.sort_column = "schedule"
        self.sort_reverse = False
        self.message = ""
        self.analysis = self.tr("builtin_no_key")
        self.running = True
        self.view_mode = "cron"
        self.metadata_store = MetadataStore()
        self._session_unlocked = False
        self._init_colors()
        self.reload()

    def _init_colors(self) -> None:
        curses.curs_set(0)
        self.stdscr.keypad(True)
        curses.use_default_colors()
        if curses.has_colors():
            curses.start_color()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLUE)
            curses.init_pair(5, curses.COLOR_RED, -1)
            curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(7, curses.COLOR_WHITE, -1)

    def reload(self) -> None:
        try:
            self.metadata_store.load()
            self.entries = list_timers(include_system=True) if self.view_mode == "systemd" else self.backend.load()
            self.apply_filter()
            self.message = self.tr("tui_loaded_entries", count=len(self.entries))
        except Exception as exc:
            self.entries = []
            self.filtered = []
            self.message = str(exc)

    def apply_filter(self) -> None:
        query = self.query.casefold().strip()
        rows = self.entries
        if query:
            def searchable(e: CronEntry) -> str:
                meta = self.metadata_store.get(e); tags = meta.get("tags", []) if isinstance(meta.get("tags"), list) else []
                return " ".join((e.schedule, humanize_schedule(e.schedule, str(self.cfg.get("language", "en"))), self._display_command(e), e.user, e.source, str(meta.get("name", "")), " ".join(str(x) for x in tags))).casefold()
            rows = [e for e in rows if query in searchable(e)]
        def key(entry: CronEntry) -> object:
            if self.sort_column == "state": return 0 if entry.enabled else 1
            if self.sort_column == "schedule": return humanize_schedule(entry.schedule, str(self.cfg.get("language", "en"))).casefold()
            if self.sort_column == "user": return entry.user.casefold()
            if self.sort_column == "source": return entry.source.casefold()
            return self._display_command(entry).casefold()
        self.filtered = sorted(rows, key=key, reverse=self.sort_reverse)
        self.index = min(self.index, max(0, len(self.filtered) - 1))

    def selected(self) -> CronEntry | None:
        if not self.filtered:
            return None
        return self.filtered[self.index]

    def _display_command(self, entry: CronEntry) -> str:
        meta = self.metadata_store.get(entry)
        return str(meta.get("original_command") or unwrap_prazycron(entry.command))

    def run(self) -> int:
        while self.running:
            self.draw()
            key = self.stdscr.getch()
            self.handle_key(key)
        return 0

    def draw(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 20 or w < 90:
            self._safe_add(0, 0, self.tr("tui_min_size"), curses.color_pair(5))
            self.stdscr.refresh()
            return
        left_w = max(58, int(w * 0.68))
        right_x = left_w + 1
        right_w = w - right_x - 1
        title = f" PrazyCron TUI  v{__version__} — {APP_TAGLINE} "
        self._box(0, 0, h - 3, w)
        self._safe_add(0, max(2, (w-len(title))//2), title, curses.color_pair(1) | curses.A_BOLD)
        mode = str(self.cfg.get("provider", "builtin")).upper() + " | " + ("SYSTEMD" if self.view_mode == "systemd" else "CRON")
        self._safe_add(1, 2, f"[ {self.tr('tui_jobs')} ] [ {self.tr('analysis')} ] [ {self.tr('backups')} ] [ {self.tr('settings')} ] [ {self.tr('tui_about')} ]", curses.color_pair(7))
        self._safe_add(1, max(2, w - len(mode) - 22), f"[ {self.tr('tui_mode')}: {mode} ]", curses.color_pair(2) | curses.A_BOLD)
        sort_name = self.tr(self.sort_column) if self.sort_column in {"state", "schedule", "user", "source", "command"} else self.sort_column
        sort_direction = self.tr("sort_desc_short") if self.sort_reverse else self.tr("sort_asc_short")
        controls = f" {self.tr('search')} {self.query or self.tr('tui_search_hint')}   {self.tr('tui_sort')}: {sort_name} {sort_direction} "
        self._safe_add(2, 2, controls[:w-4], curses.color_pair(7))
        self._hline(3, 1, w-2)
        self._vline(4, left_w, h-8)
        schedule_width = max(21, min(32, left_w - 80))
        schedule_x = 10
        user_x = schedule_x + schedule_width + 1
        source_x = user_x + 11
        command_x = source_x + 17
        command_width = max(1, left_w - command_x - 1)
        headers = [
            (2, self.tr("state"), 7, "state"),
            (schedule_x, self.tr("schedule"), schedule_width, "schedule"),
            (user_x, self.tr("user"), 10, "user"),
            (source_x, self.tr("source").rstrip(":"), 16, "source"),
            (command_x, self.tr("command"), command_width, "command"),
        ]
        for x, label, width, key_name in headers:
            if x < left_w:
                marker = " ↑" if self.sort_column == key_name else ""
                self._safe_add(4, x, (label + marker)[:width], curses.color_pair(1) | curses.A_BOLD)
        visible_h = h - 10
        if self.index < self.offset: self.offset = self.index
        if self.index >= self.offset + visible_h: self.offset = self.index - visible_h + 1
        for screen_row, entry_index in enumerate(range(self.offset, min(len(self.filtered), self.offset + visible_h)), start=5):
            entry = self.filtered[entry_index]
            selected = entry_index == self.index
            attr = curses.color_pair(4) if selected else curses.color_pair(7)
            state_attr = curses.color_pair(4) if selected else (curses.color_pair(2) if entry.enabled else curses.A_DIM)
            self._safe_add(screen_row, 2, (self.tr("on") if entry.enabled else self.tr("off")).ljust(7), state_attr | curses.A_BOLD)
            schedule_label = entry.schedule if entry.source_type.startswith("systemd_") else humanize_schedule(entry.schedule, str(self.cfg.get("language", "en")))
            self._safe_add(screen_row, schedule_x, schedule_label[:schedule_width-1].ljust(schedule_width), attr)
            self._safe_add(screen_row, user_x, entry.user[:9].ljust(10), attr)
            source = Path(entry.source).name if entry.source.startswith("/") else entry.source
            self._safe_add(screen_row, source_x, source[:15].ljust(16), attr)
            if command_x < left_w:
                self._safe_add(screen_row, command_x, self._display_command(entry)[:command_width].ljust(command_width), attr)
        total_line = f" {self.tr('total')}: {len(self.filtered)}   {self.tr('active_short')}: {sum(1 for e in self.filtered if e.enabled)}   {self.tr('inactive_short')}: {sum(1 for e in self.filtered if not e.enabled)} "
        self._safe_add(h-5, 2, total_line[:left_w-3], curses.color_pair(1))

        # Right panel: analyzer and active task details.
        self._box(4, right_x+1, max(8, h//2-3), right_w-1)
        self._safe_add(4, right_x+3, f" {self.tr('analysis')} ", curses.color_pair(3) | curses.A_BOLD)
        info = self.analysis
        if self.selected():
            selected_schedule = self.selected().schedule if self.selected().source_type.startswith('systemd_') else humanize_schedule(self.selected().schedule, str(self.cfg.get('language', 'pl')))
            info = f"{self.tr('tui_selected')}: {selected_schedule} [{self.selected().schedule}]\n{self._display_command(self.selected())}\n\n{self.analysis}"
        max_lines = max(3, h//2-7)
        for i, line in enumerate(self._wrap(info, max(10,right_w-5))[:max_lines]):
            self._safe_add(6+i, right_x+3, line, curses.color_pair(7))
        lower_y = max(13, h//2+2)
        lower_h = h - lower_y - 6
        if lower_h >= 5:
            self._box(lower_y, right_x+1, lower_h, right_w-1)
            self._safe_add(lower_y, right_x+3, f" {self.tr('tui_status_settings')} ", curses.color_pair(3) | curses.A_BOLD)
            provider = str(self.cfg.get("provider", "builtin"))
            status_lines = [f"{self.tr('provider')} {self.tr(provider)}"]
            if provider == "openai":
                status_lines.append(f"{self.tr('api_key_short')}: {self.tr('tui_api_available') if get_api_key() else self.tr('tui_api_required')}")
            elif provider == "ollama":
                status_lines.append(f"{self.tr('tui_ollama_model')}: {self.cfg.get('ollama_model', 'qwen2.5:3b')}")
            status_lines.extend([
                f"{self.tr('tui_backups')}: {BACKUP_DIR}",
                f"{self.tr('tui_root_crontab')}: {self.tr('root_loaded') if self.backend.root_loaded else self.tr('root_not_loaded')}",
            ])
            for i,line in enumerate(status_lines[:max(0,lower_h-3)]):
                self._safe_add(lower_y+2+i, right_x+3, line[:right_w-5], curses.color_pair(7))

        service_running = self.backend.service_running()
        service = self.tr("tui_running") if service_running else self.tr("tui_stopped")
        status = f" {self.tr('tui_cron_service')}: {service} | {self.message} "
        self._safe_add(h-4, 1, status[:w-2].ljust(w-2), curses.color_pair(2) if service_running else curses.color_pair(5))
        footer = " " + self.tr("tui_footer") + " "
        self._safe_add(h-2, 1, footer[:w-2].ljust(w-2), curses.color_pair(6) | curses.A_BOLD)
        self.stdscr.refresh()

    def handle_key(self, key: int) -> None:
        if key in (curses.KEY_UP, ord('k')):
            self.index = max(0, self.index-1)
        elif key in (curses.KEY_DOWN, ord('j')):
            self.index = min(max(0,len(self.filtered)-1), self.index+1)
        elif key == curses.KEY_PPAGE:
            self.index = max(0, self.index-10)
        elif key == curses.KEY_NPAGE:
            self.index = min(max(0,len(self.filtered)-1), self.index+10)
        elif key in (ord('q'), curses.KEY_F10):
            self.running = False
        elif key in (curses.KEY_F1, ord('?')):
            self.popup(self.tr("help"), self.tr("tui_help_text"))
        elif key == ord('/'):
            self.query = self.prompt(self.tr("search_title"), self.query)
            self.apply_filter()
        elif key == ord('s'):
            columns = ["state","schedule","user","source","command"]
            pos = columns.index(self.sort_column)
            if self.sort_reverse:
                self.sort_column = columns[(pos+1)%len(columns)]; self.sort_reverse=False
            else:
                self.sort_reverse=True
            self.apply_filter()
        elif key == ord('r'):
            self.reload()
        elif key == ord('v'):
            self.preview()
        elif key == ord('t'):
            self.view_mode = 'systemd' if self.view_mode == 'cron' else 'cron'; self.index = 0; self.reload()
        elif key == ord('x'):
            self.run_now()
        elif key == ord('n'):
            self.next_runs_popup()
        elif key == ord('h'):
            self.history_popup()
        elif key == ord('d'):
            self.diagnostics_popup()
        elif key == ord('c'):
            self.conflicts_popup()
        elif key == ord('e'):
            self.environment_editor()
        elif key == ord('m'):
            self.metadata_editor()
        elif key == curses.KEY_F2:
            self.add_entry()
        elif key == curses.KEY_F3:
            self.edit_entry()
        elif key == curses.KEY_F4:
            self.toggle()
        elif key == curses.KEY_F5:
            self.duplicate()
        elif key == curses.KEY_F6:
            self.explain()
        elif key == curses.KEY_F7:
            self.backups()
        elif key == curses.KEY_F8:
            self.delete()
        elif key == curses.KEY_F9:
            self.settings()

    def _authorize_change(self) -> bool:
        if bool(self.cfg.get("read_only", False)):
            self.popup(self.tr("readonly_mode"), self.tr("changes_disabled"))
            return False
        record = self.cfg.get("password_record")
        if record and not self._session_unlocked:
            password = self.prompt(self.tr("prazycron_password"), "", secret=True)
            if not verify_password(password, record if isinstance(record, dict) else None):
                self.popup(self.tr("access_denied"), self.tr("wrong_password"))
                return False
            self._session_unlocked = True
        return True

    def add_entry(self) -> None:
        if not self._authorize_change(): return
        if self.view_mode == "systemd": self._systemd_editor(None)
        else: self._entry_editor(None)

    def edit_entry(self) -> None:
        entry = self.selected()
        if not entry or not self._authorize_change(): return
        if entry.source_type.startswith("systemd_"): self._systemd_editor(entry); return
        if not entry.editable:
            self.popup(self.tr("information"), self.tr("not_editable")); return
        self._entry_editor(entry)

    def _entry_editor(self, entry: CronEntry | None) -> None:
        """Full-screen-like TUI form matching the fields available in the GUI editor."""
        if entry is None:
            destinations = self.backend.available_destinations(self.backend.root_loaded)
        else:
            # Editing never changes the source. Keep the real source even for /etc/cron.d files,
            # which are not listed as generic add destinations.
            destinations = [(entry.source_name, entry.source, entry.source_type)]
        if not destinations:
            self.popup(self.tr("error"), self.tr("tui_no_destinations"))
            return
        destination_index = 0
        meta = self.metadata_store.get(entry) if entry else {}
        entry_id = str(meta.get("entry_id") or uuid.uuid4())
        schedule = entry.schedule if entry else "0 3 * * *"
        command = str(meta.get("original_command") or (unwrap_prazycron(entry.command) if entry else ""))
        user = entry.user if entry else getpass.getuser()
        enabled = entry.enabled if entry else True
        history_enabled = bool(meta.get("history_enabled", False))
        prevent_overlap = bool(meta.get("prevent_overlap", False))
        preset_index = next((i for i, (_name, value) in enumerate(TUI_PRESETS) if value and value == schedule), 0)
        selected = 0

        while True:
            _backend_label, source, source_type = destinations[destination_index]
            if source_type == "user_crontab":
                _dest_label = self.tr("destination_user")
            elif source_type == "root_crontab":
                _dest_label = self.tr("destination_root")
            elif source == "/etc/crontab":
                _dest_label = self.tr("destination_system")
            else:
                _dest_label = source
            if source_type == "user_crontab":
                user = getpass.getuser()
            elif source_type == "root_crontab":
                user = "root"
            user_editable = source_type in {"system_file", "cron_d"}

            fields = [
                ("destination", self.tr("source_type"), _dest_label),
                ("preset", self.tr("presets"), self.tr(TUI_PRESETS[preset_index][0])),
                ("schedule", self.tr("raw_schedule"), schedule),
                ("user", self.tr("user"), user + ("" if user_editable else "  " + self.tr("readonly_label"))),
                ("command", self.tr("command"), command),
                ("history", self.tr("history_option"), self.tr("on") if history_enabled else self.tr("off")),
                ("lock", self.tr("parallel_option"), self.tr("on") if prevent_overlap else self.tr("off")),
                ("enabled", self.tr("enabled"), self.tr("on") if enabled else self.tr("off")),
                ("save", self.tr("save"), ""),
                ("cancel", self.tr("cancel"), ""),
            ]
            h, w = self.stdscr.getmaxyx()
            width = min(108, max(72, w - 8))
            height = min(24, max(17, h - 6))
            y = max(1, (h - height) // 2)
            x = max(1, (w - width) // 2)
            win = curses.newwin(height, width, y, x)
            win.keypad(True)
            win.erase(); win.box()
            title = f" {self.tr('entry_editor')} "
            try:
                win.addstr(0, max(2, (width - len(title)) // 2), title, curses.color_pair(1) | curses.A_BOLD)
                win.addstr(2, 3, self.tr("tui_editor_help")[:width-6], curses.A_DIM)
            except curses.error:
                pass

            row = 4
            label_width = 24
            for idx, (_key, label, value) in enumerate(fields):
                attr = curses.color_pair(4) if idx == selected else curses.color_pair(7)
                marker = ">" if idx == selected else " "
                if value:
                    available = max(10, width - label_width - 9)
                    shown = value.replace("\n", " ")
                    if len(shown) > available:
                        shown = shown[: max(1, available - 1)] + "…"
                    text = f"{marker} {label:<{label_width}} {shown}"
                else:
                    text = f"{marker} {label}"
                try:
                    win.addstr(row + idx, 3, text[:width-6].ljust(width-6), attr)
                except curses.error:
                    pass

            try:
                win.addstr(height-3, 3, f"{self.tr('source')[:-1]}: {source}"[:width-6], curses.A_DIM)
                win.addstr(height-2, 3, self.tr("tui_escape_cancel")[:width-6], curses.A_DIM)
            except curses.error:
                pass
            win.refresh()
            key = win.getch()

            if key in (27, ord('q')):
                self.tr.set_language(str(self.cfg.get("language", "en")))
                return
            if key in (curses.KEY_UP, ord('k'), curses.KEY_BTAB):
                selected = (selected - 1) % len(fields)
                continue
            if key in (curses.KEY_DOWN, ord('j'), 9):
                selected = (selected + 1) % len(fields)
                continue

            field_key = fields[selected][0]
            direction = -1 if key == curses.KEY_LEFT else 1
            if field_key == "destination" and entry is None and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 10, 13, ord(' ')):
                destination_index = (destination_index + direction) % len(destinations)
                continue
            if field_key == "preset" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 10, 13, ord(' ')):
                preset_index = (preset_index + direction) % len(TUI_PRESETS)
                preset_value = TUI_PRESETS[preset_index][1]
                if preset_value:
                    schedule = preset_value
                continue
            if field_key == "enabled" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 10, 13, ord(' ')):
                enabled = not enabled
                continue
            if field_key == "history" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 10, 13, ord(' ')):
                history_enabled = not history_enabled
                continue
            if field_key == "lock" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 10, 13, ord(' ')):
                prevent_overlap = not prevent_overlap
                continue
            if key not in (10, 13):
                continue

            if field_key == "schedule":
                value = self.prompt(self.tr("raw_schedule"), schedule)
                if value:
                    schedule = value.strip()
                    preset_index = next((i for i, (_name, preset) in enumerate(TUI_PRESETS) if preset and preset == schedule), 0)
            elif field_key == "user":
                if user_editable:
                    value = self.prompt(self.tr("user"), user)
                    if value:
                        user = value.strip()
                else:
                    self.popup(self.tr("information"), self.tr("tui_user_fixed"))
            elif field_key == "command":
                value = self.prompt(self.tr("command"), command)
                if value:
                    command = value.strip()
            elif field_key == "cancel":
                return
            elif field_key == "save":
                try:
                    issues = validate_entry(schedule, command, user, source_type=source_type)
                    errors = [issue for issue in issues if issue.severity == "error"]
                    if errors:
                        self.popup(self.tr("validation"), "\n".join(f"{self.tr('validation_error_prefix')}: {issue.message}" for issue in issues)); continue
                    if issues:
                        self.popup(self.tr("validation_warnings"), "\n".join(f"{self.tr('validation_warning_prefix') if issue.severity == 'warning' else issue.severity.upper()}: {issue.message}" for issue in issues))
                        if self.prompt(self.tr("continue_save_prompt"), "") != "SAVE": continue
                    managed = command
                    if history_enabled or prevent_overlap:
                        args = ["/usr/bin/prazycron-run", "--id", entry_id, "--history-dir", str(HISTORY_DIR), "--owner", getpass.getuser()]
                        if prevent_overlap: args += ["--lock", entry_id]
                        if not history_enabled: args += ["--no-history"]
                        args += ["--", "/bin/sh", "-c", command]
                        managed = shlex.join(args)
                    if entry is None: _before, _after, diff = self.backend.preview_add(source, source_type, schedule, managed, user, enabled)
                    else: _before, _after, diff = self.backend.preview_edit(entry, schedule, managed, user, enabled)
                    self.popup(self.tr("change_preview"), diff)
                    if self.prompt(self.tr("apply_save_prompt"), "") != "APPLY": continue
                    if entry is None: self.backend.add_entry(source, source_type, schedule, managed, user, enabled)
                    else: self.backend.edit_entry(entry, schedule, managed, user, enabled)
                    fresh = self.backend.load(include_root=self.backend.root_loaded)
                    candidates = [item for item in fresh if item.source == source and item.source_type == source_type and item.schedule == schedule and item.command == managed]
                    new_entry = candidates[-1] if candidates else None
                    if entry: self.metadata_store.remove(entry)
                    if new_entry:
                        self.metadata_store.update(new_entry, entry_id=entry_id, history_enabled=history_enabled, prevent_overlap=prevent_overlap, original_command=command if managed != command else "")
                    self.reload(); self.message = self.tr("changes_saved"); return
                except Exception as exc:
                    self.popup(self.tr("error"), str(exc))

    def toggle(self) -> None:
        entry = self.selected()
        if not entry or not self._authorize_change(): return
        try:
            if entry.source_type.startswith("systemd_"): toggle_timer(entry)
            else: self.backend.toggle_entry(entry)
            self.reload(); self.message = self.tr("state_changed_backup")
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def duplicate(self) -> None:
        entry = self.selected()
        if not entry or not self._authorize_change(): return
        if entry.source_type.startswith("systemd_"): self._systemd_editor(None); return
        try:
            self.backend.duplicate_entry(entry); self.reload(); self.message = self.tr("duplicated_backup")
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def delete(self) -> None:
        entry = self.selected()
        if not entry or not self._authorize_change(): return
        if not entry.source_type.startswith("systemd_"):
            try: self.popup(self.tr("delete_preview"), self.backend.preview_delete(entry))
            except Exception as exc: self.popup(self.tr("error"), str(exc)); return
        if self.prompt(self.tr("delete_confirm_prompt"), "") != "DELETE": return
        try:
            if entry.source_type.startswith("systemd_"): delete_timer(entry)
            else: self.backend.delete_entry(entry)
            self.metadata_store.remove(entry); self.reload(); self.message = self.tr("deleted_backup")
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def explain(self) -> None:
        entry = self.selected()
        if not entry: return
        provider = str(self.cfg.get("provider","builtin"))
        try:
            if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
                self.popup(self.tr("api_prompt_title"), self.tr("api_key_environment_help"))
                return
            self.analysis = ai_explain(entry, self.cfg, provider)
            self.popup(self.tr("analysis"), self.analysis)
        except AIError as exc:
            self.popup(self.tr("ai_error"), str(exc))

    def preview(self) -> None:
        entry = self.selected()
        if not entry: return
        try:
            self.popup(self.tr("source_title", source=entry.source), self.backend.source_text(entry))
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def backups(self) -> None:
        records = self.backend.list_backups()
        if not records:
            self.popup(self.tr("backups"), f"{self.tr('no_backups')}\n{self.tr('folder')}: {BACKUP_DIR}")
            return
        lines = [f"{i+1:>3}. {r.get('created')}  {r.get('source')}  {r.get('size')} bytes" for i,r in enumerate(records[:100])]
        self.popup(self.tr("backups"), "\n".join(lines))
        choice = self.prompt(self.tr("backup_number_prompt"), "")
        if not choice: return
        try:
            record = records[int(choice)-1]
            confirm = self.prompt(self.tr("restore_confirm_prompt"), "")
            if confirm != "RESTORE": return
            self.backend.restore_backup(record); self.reload(); self.message = self.tr("backup_restored")
        except (ValueError, IndexError, BackendError) as exc:
            self.popup(self.tr("error"), str(exc))

    def _systemd_editor(self, entry: CronEntry | None) -> None:
        if entry and not bool(entry.metadata.get("managed", False)):
            self.popup(self.tr("systemd_timer"), self.tr("managed_timer_only"))
            return
        name = self.prompt(self.tr("timer_name"), entry.source[:-6].replace("prazycron-", "") if entry else self.tr("new_task_name"))
        if not name: return
        calendar = self.prompt(self.tr("on_calendar_example"), entry.schedule if entry else "daily")
        if not calendar: return
        command = self.prompt(self.tr("command"), entry.command if entry else "")
        if not command: return
        scope = self.prompt(self.tr("scope_prompt"), "user" if not entry or entry.source_type == "systemd_user_timer" else "system")
        persistent = self.prompt(self.tr("persistent_prompt"), "yes")
        random_delay = self.prompt(self.tr("random_delay_prompt"), "")
        if self.prompt(self.tr("apply_save_prompt"), "") != "APPLY": return
        try:
            if entry: delete_timer(entry)
            create_timer(name, calendar, command, user_scope=scope.lower() != "system", persistent=persistent.lower() not in {"no", "n", "0"}, random_delay=random_delay)
            self.reload(); self.message = self.tr("timer_saved")
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def run_now(self) -> None:
        entry = self.selected()
        if not entry or not self._authorize_change(): return
        if self.prompt(self.tr("run_confirm_prompt"), "") != "RUN": return
        try:
            if entry.source_type.startswith("systemd_"):
                unit = str(entry.metadata.get("service_unit", entry.source[:-6] + ".service"))
                args = ["systemctl", "--user", "start", unit] if entry.source_type == "systemd_user_timer" else ["pkexec", "systemctl", "start", unit]
                proc = subprocess.run(args, text=True, capture_output=True, check=False)
                self.popup(self.tr("run_result"), (proc.stdout or "") + (proc.stderr or "") + f"\n{self.tr('exit_code')}: {proc.returncode}")
            else:
                meta = self.metadata_store.get(entry)
                command = str(meta.get("original_command") or unwrap_prazycron(entry.command))
                record = self.backend.run_now(replace(entry, command=command), timeout=int(self.cfg.get("run_timeout", 3600)), entry_id=str(meta.get("entry_id") or entry_signature(entry)))
                self.popup(self.tr("run_result"), f"{self.tr('exit_code')}: {record.exit_code}\n{self.tr('duration')}: {record.duration_seconds:.3f}s\n\n{self.tr('stdout')}\n{record.stdout or self.tr('empty')}\n\n{self.tr('stderr')}\n{record.stderr or self.tr('empty')}")
                self.reload()
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def next_runs_popup(self) -> None:
        entry = self.selected()
        if not entry: return
        if entry.source_type.startswith("systemd_"):
            self.popup(self.tr("systemd_timer"), f"{self.tr('unit')}: {entry.source}\nOnCalendar: {entry.schedule}\n{self.tr('command')}: {entry.command}"); return
        meta = self.metadata_store.get(entry); timezone_name = str(meta.get("timezone") or system_timezone_name())
        try:
            runs = next_runs(entry.schedule, 10, timezone_name=timezone_name)
            text = f"{self.tr('timezone')}: {timezone_name}\n\n" + ("\n".join(f"{i}. {run.isoformat()}" for i, run in enumerate(runs, 1)) if runs else "@reboot")
            note = dst_note(entry.schedule, timezone_name)
            if note: text += "\n\n" + self.tr("warning") + ": " + note
            self.popup(self.tr("next_runs_title"), text)
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def history_popup(self) -> None:
        entry = self.selected()
        if not entry: return
        meta = self.metadata_store.get(entry); history_id = str(meta.get("entry_id") or entry_signature(entry)); records = read_history(history_id, limit=int(self.cfg.get("history_limit", 200)))
        if not records:
            self.popup(self.tr("execution_history"), self.tr("no_saved_executions_tui")); return
        text = "\n\n".join(f"{rec.finished} code={rec.exit_code} duration={rec.duration_seconds:.3f}s mode={rec.mode}\nSTDOUT: {(rec.stdout or '(empty)')[:1200]}\nSTDERR: {(rec.stderr or '(empty)')[:1200]}" for rec in records)
        self.popup(self.tr("execution_history"), text)

    def diagnostics_popup(self) -> None:
        try:
            entries = self.backend.load(include_root=self.backend.root_loaded)
            items = diagnose(self.backend, entries)
            self.popup(self.tr("diagnostics_title"), "\n".join(f"[{item.severity.upper()}] {item.subject}: {item.message}" for item in items))
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def conflicts_popup(self) -> None:
        try:
            conflicts = detect_conflicts(self.backend.load(include_root=self.backend.root_loaded))
            self.popup(self.tr("conflicts_title"), self.tr("no_conflicts_short") if not conflicts else "\n".join(item.message for item in conflicts))
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def environment_editor(self) -> None:
        entry = self.selected()
        if not entry or not self._authorize_change(): return
        if entry.source_type.startswith("systemd_") or entry.source_type == "directory_script":
            self.popup(self.tr("environment"), self.tr("environment_unsupported")); return
        try:
            original = self.backend.source_text(entry); values = parse_environment(original)
            for name in KNOWN_ENV:
                value = self.prompt(name, values.get(name, ""))
                values[name] = value
            extra = self.prompt(self.tr("additional_env_inline"), "")
            for item in extra.split(";"):
                if "=" in item:
                    name, value = item.split("=", 1); values[name.strip()] = value.strip()
            changed = update_environment(original, values); self.popup(self.tr("environment_diff"), self.backend.unified_diff(original, changed, entry.source))
            if self.prompt(self.tr("apply_save_prompt"), "") != "APPLY": return
            self.backend.update_source_environment(entry, dict(values)); self.reload(); self.message = self.tr("environment_saved")
        except Exception as exc: self.popup(self.tr("error"), str(exc))

    def metadata_editor(self) -> None:
        entry = self.selected()
        if not entry or not self._authorize_change(): return
        meta = self.metadata_store.get(entry)
        name = self.prompt(self.tr("task_name_prompt"), str(meta.get("name", "")))
        tags = self.prompt(self.tr("tags_prompt"), ", ".join(meta.get("tags", []) if isinstance(meta.get("tags"), list) else []))
        favorite = self.prompt(self.tr("favorite_prompt"), "yes" if meta.get("favorite") else "no")
        timezone_name = self.prompt(self.tr("analysis_timezone_prompt"), str(meta.get("timezone") or system_timezone_name()))
        self.metadata_store.update(entry, name=name.strip(), tags=[item.strip() for item in tags.split(",") if item.strip()], favorite=favorite.lower() in {"yes", "y", "1", "true"}, timezone=timezone_name.strip())
        self.reload(); self.message = self.tr("metadata_saved")

    def settings(self) -> None:
        """Open a keyboard-driven settings dialog with language and AI provider selection."""
        provider_values = ["builtin", "ollama", "openai"]
        provider = str(self.cfg.get("provider", "builtin"))
        if provider not in provider_values:
            provider = "builtin"
        language_values = list(LANGUAGES)
        language = str(self.cfg.get("language", "en"))
        if language not in language_values:
            language = "pl"
        ollama_model = str(self.cfg.get("ollama_model", "qwen2.5:3b"))
        ollama_endpoint = str(self.cfg.get("ollama_endpoint", "http://127.0.0.1:11434/api/generate"))
        openai_model = str(self.cfg.get("openai_model", "gpt-5-mini"))
        openai_endpoint = str(self.cfg.get("openai_endpoint", "https://api.openai.com/v1/responses"))
        api_key_entered = False
        selected = 0

        while True:
            h, w = self.stdscr.getmaxyx()
            width = min(92, max(70, w - 10))
            height = min(24, max(17, h - 8))
            y = max(1, (h - height) // 2)
            x = max(1, (w - width) // 2)
            win = curses.newwin(height, width, y, x)
            win.keypad(True)

            fields: list[tuple[str, str, str]] = [
                ("language", self.tr("language"), LANGUAGES[language]),
                ("provider", self.tr("provider"), self.tr(provider)),
            ]
            if provider == "ollama":
                fields.extend([
                    ("ollama_model", "Ollama " + self.tr("model"), ollama_model),
                    ("ollama_endpoint", "Ollama " + self.tr("endpoint"), ollama_endpoint),
                ])
            elif provider == "openai":
                fields.extend([
                    ("openai_model", "OpenAI " + self.tr("model"), openai_model),
                    ("openai_endpoint", "OpenAI " + self.tr("endpoint"), openai_endpoint),
                    ("api_key", self.tr("api_key"), self.tr("tui_entered") if (api_key_entered or get_api_key()) else self.tr("tui_not_entered")),
                ])
            fields.extend([
                ("save", self.tr("tui_save_close"), ""),
                ("cancel", self.tr("cancel"), ""),
            ])
            selected = min(selected, len(fields) - 1)

            win.erase(); win.box()
            title = f" {self.tr('settings')} "
            try:
                win.addstr(0, max(2, (width - len(title)) // 2), title, curses.color_pair(1) | curses.A_BOLD)
                win.addstr(2, 3, self.tr("tui_settings_help")[:width-6], curses.A_DIM)
            except curses.error:
                pass

            row = 4
            for idx, (_key, label, value) in enumerate(fields):
                attr = curses.color_pair(4) if idx == selected else curses.color_pair(7)
                marker = ">" if idx == selected else " "
                if value:
                    text = f"{marker} {label:<31} {value}"
                else:
                    text = f"{marker} {label}"
                try:
                    win.addstr(row + idx, 3, text[:width-6].ljust(width-6), attr)
                except curses.error:
                    pass

            note = self.tr("builtin_no_key")
            if provider == "openai":
                note = self.tr("tui_openai_key_note")
            elif provider == "ollama":
                note = self.tr("ollama_no_key")
            try:
                win.addstr(height-3, 3, note[:width-6], curses.color_pair(3))
                win.addstr(height-2, 3, self.tr("tui_escape_cancel")[:width-6], curses.A_DIM)
            except curses.error:
                pass
            win.refresh()

            key = win.getch()
            if key in (27, ord('q')):
                self.tr.set_language(str(self.cfg.get("language", "en")))
                return
            if key in (curses.KEY_UP, ord('k'), curses.KEY_BTAB):
                selected = (selected - 1) % len(fields)
                continue
            if key in (curses.KEY_DOWN, ord('j'), 9):
                selected = (selected + 1) % len(fields)
                continue

            field_key = fields[selected][0]
            if field_key == "language" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 10, 13, ord(' ')):
                direction = -1 if key == curses.KEY_LEFT else 1
                language = language_values[(language_values.index(language) + direction) % len(language_values)]
                self.tr.set_language(language)
                selected = 0
                continue
            if field_key == "provider" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 10, 13, ord(' ')):
                direction = -1 if key == curses.KEY_LEFT else 1
                provider = provider_values[(provider_values.index(provider) + direction) % len(provider_values)]
                selected = 1
                continue
            if key not in (10, 13):
                continue

            if field_key == "ollama_model":
                value = self.prompt("Ollama " + self.tr("model"), ollama_model)
                if value:
                    ollama_model = value
            elif field_key == "ollama_endpoint":
                value = self.prompt("Ollama " + self.tr("endpoint"), ollama_endpoint)
                if value:
                    ollama_endpoint = value
            elif field_key == "openai_model":
                value = self.prompt("OpenAI " + self.tr("model"), openai_model)
                if value:
                    openai_model = value
            elif field_key == "openai_endpoint":
                value = self.prompt("OpenAI " + self.tr("endpoint"), openai_endpoint)
                if value:
                    openai_endpoint = value
            elif field_key == "api_key":
                value = self.prompt(self.tr("api_key"), "", secret=True)
                if value:
                    set_session_api_key(value)
                    api_key_entered = True
            elif field_key == "cancel":
                self.tr.set_language(str(self.cfg.get("language", "en")))
                return
            elif field_key == "save":
                self.cfg.update({
                    "language": language,
                    "language_explicit": True,
                    "provider": provider,
                    "ollama_model": ollama_model,
                    "ollama_endpoint": ollama_endpoint,
                    "openai_model": openai_model,
                    "openai_endpoint": openai_endpoint,
                })
                save_config(self.cfg)
                self.tr.set_language(language)
                self.analysis = self.tr("builtin_no_key") if provider == "builtin" else self.analysis
                self.message = self.tr("tui_settings_saved")
                return

    def prompt(self, title: str, initial: str = "", secret: bool = False) -> str:
        h, w = self.stdscr.getmaxyx()
        width = min(max(52, len(title)+8), w-4)
        y = max(1, h//2-3); x = max(1, (w-width)//2)
        win = curses.newwin(6, width, y, x)
        win.keypad(True); win.box()
        win.addstr(0, 2, f" {title[:width-6]} ", curses.A_BOLD)
        value = list(initial)
        pos = len(value)
        curses.curs_set(1)
        while True:
            raw_display = "".join(value)
            display = "*" * len(raw_display) if secret else raw_display
            win.addstr(2, 2, " "*(width-4))
            start = max(0, pos-(width-6))
            shown = display[start:start+width-5]
            win.addstr(2, 2, shown)
            win.move(2, 2+min(pos-start, width-6)); win.refresh()
            key = win.getch()
            if key in (10,13):
                break
            if key == 27:
                value=[]; break
            if key in (curses.KEY_BACKSPACE,127,8) and pos>0:
                value.pop(pos-1); pos-=1
            elif key == curses.KEY_DC and pos<len(value): value.pop(pos)
            elif key == curses.KEY_LEFT: pos=max(0,pos-1)
            elif key == curses.KEY_RIGHT: pos=min(len(value),pos+1)
            elif 32 <= key <= 0x10ffff:
                try:
                    ch=chr(key); value.insert(pos,ch); pos+=1
                except ValueError: pass
        curses.curs_set(0)
        return "".join(value)

    def popup(self, title: str, text: str) -> None:
        h,w = self.stdscr.getmaxyx(); ph=max(8,min(h-4,int(h*0.78))); pw=max(50,min(w-4,int(w*0.82)))
        y=(h-ph)//2; x=(w-pw)//2; win=curses.newwin(ph,pw,y,x); win.keypad(True)
        lines=[]
        for raw in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(raw, max(10,pw-4), replace_whitespace=False, drop_whitespace=False) or [""])
        offset=0
        while True:
            win.erase(); win.box(); win.addstr(0,2,f" {title[:pw-6]} ",curses.A_BOLD)
            for i,line in enumerate(lines[offset:offset+ph-4]):
                try: win.addstr(2+i,2,line[:pw-4])
                except curses.error: pass
            hint=self.tr("tui_scroll_close"); win.addstr(ph-2,max(2,pw-len(hint)-2),hint[:pw-4],curses.A_DIM); win.refresh()
            key=win.getch()
            if key in (10,13,27,ord('q')): break
            if key in (curses.KEY_DOWN,ord('j')): offset=min(max(0,len(lines)-(ph-4)),offset+1)
            elif key in (curses.KEY_UP,ord('k')): offset=max(0,offset-1)
            elif key==curses.KEY_NPAGE: offset=min(max(0,len(lines)-(ph-4)),offset+ph-4)
            elif key==curses.KEY_PPAGE: offset=max(0,offset-(ph-4))

    def _box(self, y:int, x:int, height:int, width:int) -> None:
        if height < 2 or width < 2: return
        try:
            self.stdscr.addch(y,x,curses.ACS_ULCORNER); self.stdscr.hline(y,x+1,curses.ACS_HLINE,width-2); self.stdscr.addch(y,x+width-1,curses.ACS_URCORNER)
            self.stdscr.vline(y+1,x,curses.ACS_VLINE,height-2); self.stdscr.vline(y+1,x+width-1,curses.ACS_VLINE,height-2)
            self.stdscr.addch(y+height-1,x,curses.ACS_LLCORNER); self.stdscr.hline(y+height-1,x+1,curses.ACS_HLINE,width-2); self.stdscr.addch(y+height-1,x+width-1,curses.ACS_LRCORNER)
        except curses.error: pass

    def _hline(self,y:int,x:int,n:int) -> None:
        try:self.stdscr.hline(y,x,curses.ACS_HLINE,n)
        except curses.error:pass
    def _vline(self,y:int,x:int,n:int) -> None:
        try:self.stdscr.vline(y,x,curses.ACS_VLINE,n)
        except curses.error:pass
    def _safe_add(self,y:int,x:int,text:str,attr:int=0) -> None:
        h,w=self.stdscr.getmaxyx()
        if y<0 or y>=h or x<0 or x>=w:return
        try:self.stdscr.addstr(y,x,text[:max(0,w-x-1)],attr)
        except curses.error:pass
    @staticmethod
    def _wrap(text:str,width:int)->list[str]:
        lines=[]
        for raw in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(raw,width) or [""])
        return lines


def run_tui() -> int:
    return curses.wrapper(lambda stdscr: TUI(stdscr).run())
