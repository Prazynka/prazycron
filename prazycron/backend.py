from __future__ import annotations

import difflib
import getpass
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import BACKUP_DIR
from .environment import parse_environment, update_environment
from .execution import ExecutionRecord, run_command
from .metadata import entry_signature
from .cron import append_line, parse_crontab_text, parse_directory, render_line, replace_line, toggle_line
from .model import CronEntry

SYSTEM_SOURCES = [
    ("/etc/crontab", "system_file"),
]
CRON_DIRS = [
    ("/etc/cron.hourly", "@hourly"),
    ("/etc/cron.daily", "@daily"),
    ("/etc/cron.weekly", "@weekly"),
    ("/etc/cron.monthly", "@monthly"),
]


class BackendError(RuntimeError):
    pass


class CronBackend:
    def __init__(self) -> None:
        self.root_loaded = False
        self._root_text: str | None = None
        self.entries: list[CronEntry] = []

    @staticmethod
    def _run(args: list[str], input_text: str | None = None, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(
                args, input=input_text, text=True, capture_output=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(str(exc)) from exc
        if check and proc.returncode != 0:
            message = (proc.stderr or proc.stdout or f"Command failed: {' '.join(args)}").strip()
            raise BackendError(message)
        return proc

    def read_user_crontab(self) -> str:
        proc = self._run(["crontab", "-l"], check=False)
        if proc.returncode == 0:
            return proc.stdout
        stderr = (proc.stderr or "").lower()
        if "no crontab" in stderr:
            return ""
        raise BackendError((proc.stderr or proc.stdout).strip())

    def read_root_crontab(self, authenticate: bool = True) -> str:
        args = ["pkexec", "crontab", "-u", "root", "-l"] if authenticate and os.geteuid() != 0 else ["crontab", "-u", "root", "-l"]
        proc = self._run(args, check=False, timeout=90)
        if proc.returncode == 0:
            self.root_loaded = True
            self._root_text = proc.stdout
            return proc.stdout
        stderr = (proc.stderr or "").lower()
        if "no crontab" in stderr:
            self.root_loaded = True
            self._root_text = ""
            return ""
        raise BackendError((proc.stderr or proc.stdout or "Unable to read root crontab").strip())

    @staticmethod
    def read_file(path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise BackendError(f"{path}: {exc}") from exc

    def load(self, include_root: bool | None = None) -> list[CronEntry]:
        if include_root is None:
            include_root = self.root_loaded
        entries: list[CronEntry] = []
        user = getpass.getuser()
        try:
            text = self.read_user_crontab()
            entries.extend(parse_crontab_text(text, "crontab:user", "user_crontab", user))
        except BackendError:
            pass

        if include_root and self.root_loaded:
            text = self._root_text if self._root_text is not None else self.read_root_crontab(authenticate=False)
            entries.extend(parse_crontab_text(text, "crontab:root", "root_crontab", "root"))

        for path, source_type in SYSTEM_SOURCES:
            if Path(path).is_file():
                try:
                    entries.extend(parse_crontab_text(self.read_file(path), path, source_type, "root"))
                except BackendError:
                    pass

        cron_d = Path("/etc/cron.d")
        if cron_d.is_dir():
            try:
                files = sorted(p for p in cron_d.iterdir() if p.is_file() and not p.name.startswith(".") and not p.name.endswith("~"))
            except OSError:
                files = []
            for path in files:
                try:
                    entries.extend(parse_crontab_text(self.read_file(str(path)), str(path), "cron_d", "root"))
                except BackendError:
                    continue

        for directory, schedule in CRON_DIRS:
            entries.extend(parse_directory(directory, schedule))

        self.entries = entries
        return entries

    def service_running(self) -> bool:
        for service in ("cron.service", "crond.service"):
            proc = self._run(["systemctl", "is-active", "--quiet", service], check=False, timeout=5)
            if proc.returncode == 0:
                return True
        return False

    def start_service(self) -> None:
        service = "cron.service"
        self._run(["pkexec", "systemctl", "start", service] if os.geteuid() != 0 else ["systemctl", "start", service], timeout=90)

    def _read_source(self, entry_or_source: CronEntry | tuple[str, str]) -> tuple[str, str, str]:
        if isinstance(entry_or_source, CronEntry):
            source = entry_or_source.source
            source_type = entry_or_source.source_type
        else:
            source, source_type = entry_or_source
        if source_type == "user_crontab":
            return self.read_user_crontab(), source, source_type
        if source_type == "root_crontab":
            text = self._root_text if self._root_text is not None else self.read_root_crontab()
            return text, source, source_type
        if source_type in {"system_file", "cron_d"}:
            return self.read_file(source), source, source_type
        if source_type == "directory_script":
            return self.read_file(source), source, source_type
        raise BackendError(f"Unknown source type: {source_type}")

    def source_text(self, entry: CronEntry) -> str:
        return self._read_source(entry)[0]

    def _backup_name(self, source: str, source_type: str) -> str:
        safe = source.replace("/", "_").replace(":", "_").strip("_") or "source"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return f"{timestamp}__{source_type}__{safe}"

    def create_backup(self, source: str, source_type: str, text: str | None = None) -> Path:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        if text is None:
            if source_type == "directory_script":
                try:
                    data = Path(source).read_bytes()
                except OSError as exc:
                    raise BackendError(str(exc)) from exc
                base = BACKUP_DIR / self._backup_name(source, source_type)
                payload = base.with_suffix(".bin")
                payload.write_bytes(data)
                mode = stat.S_IMODE(Path(source).stat().st_mode)
                meta = {"source": source, "source_type": source_type, "payload": payload.name, "binary": True, "mode": mode}
            else:
                text = self._read_source((source, source_type))[0]
        if text is not None:
            base = BACKUP_DIR / self._backup_name(source, source_type)
            payload = base.with_suffix(".txt")
            payload.write_text(text, encoding="utf-8")
            meta = {"source": source, "source_type": source_type, "payload": payload.name, "binary": False}
        metadata = payload.with_suffix(payload.suffix + ".json")
        metadata.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return metadata

    def list_backups(self) -> list[dict[str, object]]:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, object]] = []
        for meta_path in sorted(BACKUP_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                payload = BACKUP_DIR / str(data["payload"])
                data["metadata_path"] = str(meta_path)
                data["payload_path"] = str(payload)
                data["created"] = datetime.fromtimestamp(meta_path.stat().st_mtime).isoformat(sep=" ", timespec="seconds")
                data["size"] = payload.stat().st_size if payload.exists() else 0
                records.append(data)
            except (OSError, KeyError, json.JSONDecodeError):
                continue
        return records

    def _write_text_source(self, source: str, source_type: str, text: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="prazycron-", suffix=".tmp") as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        os.chmod(tmp_path, 0o644)
        try:
            if source_type == "user_crontab":
                self._run(["crontab", tmp_path])
            elif source_type == "root_crontab":
                args = ["crontab", "-u", "root", tmp_path]
                if os.geteuid() != 0:
                    args.insert(0, "pkexec")
                self._run(args, timeout=90)
                self._root_text = text
                self.root_loaded = True
            elif source_type in {"system_file", "cron_d"}:
                target = Path(source)
                try:
                    mode = stat.S_IMODE(target.stat().st_mode)
                except OSError:
                    mode = 0o644
                if os.access(target, os.W_OK):
                    shutil.copyfile(tmp_path, target)
                    os.chmod(target, mode)
                else:
                    args = ["install", "-m", f"{mode:04o}", tmp_path, source]
                    if os.geteuid() != 0:
                        args.insert(0, "pkexec")
                    self._run(args, timeout=90)
            else:
                raise BackendError("Source is not a writable crontab")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def add_entry(self, source: str, source_type: str, schedule: str, command: str, user: str, enabled: bool = True) -> None:
        text, _, _ = self._read_source((source, source_type))
        self.create_backup(source, source_type, text)
        system_format = source_type in {"system_file", "cron_d"}
        line = render_line(schedule, command, user, system_format, enabled)
        self._write_text_source(source, source_type, append_line(text, line))

    def edit_entry(self, entry: CronEntry, schedule: str, command: str, user: str, enabled: bool) -> None:
        if not entry.editable or entry.line_index is None:
            raise BackendError("This entry cannot be edited as a crontab line")
        text, source, source_type = self._read_source(entry)
        self.create_backup(source, source_type, text)
        system_format = source_type in {"system_file", "cron_d"}
        line = render_line(schedule, command, user, system_format, enabled)
        self._write_text_source(source, source_type, replace_line(text, entry.line_index, line))

    def duplicate_entry(self, entry: CronEntry) -> None:
        if not entry.editable:
            raise BackendError("Directory scripts cannot be duplicated from this application")
        self.add_entry(entry.source, entry.source_type, entry.schedule, entry.command, entry.user, entry.enabled)

    def toggle_entry(self, entry: CronEntry) -> None:
        if entry.source_type == "directory_script":
            self.create_backup(entry.source, entry.source_type)
            try:
                current = stat.S_IMODE(Path(entry.source).stat().st_mode)
            except OSError as exc:
                raise BackendError(str(exc)) from exc
            new_mode = current & ~0o111 if entry.enabled else current | 0o111
            if os.access(entry.source, os.W_OK):
                os.chmod(entry.source, new_mode)
            else:
                args = ["chmod", f"{new_mode:04o}", entry.source]
                if os.geteuid() != 0:
                    args.insert(0, "pkexec")
                self._run(args, timeout=90)
            return
        if entry.line_index is None:
            raise BackendError("Entry has no source line")
        text, source, source_type = self._read_source(entry)
        self.create_backup(source, source_type, text)
        lines = text.splitlines()
        if entry.line_index >= len(lines):
            raise BackendError("Source changed; refresh the list")
        lines[entry.line_index] = toggle_line(lines[entry.line_index], not entry.enabled)
        new_text = "\n".join(lines) + ("\n" if lines else "")
        self._write_text_source(source, source_type, new_text)

    def delete_entry(self, entry: CronEntry) -> None:
        if entry.source_type == "directory_script":
            self.create_backup(entry.source, entry.source_type)
            if os.access(entry.source, os.W_OK):
                Path(entry.source).unlink()
            else:
                args = ["rm", "--", entry.source]
                if os.geteuid() != 0:
                    args.insert(0, "pkexec")
                self._run(args, timeout=90)
            return
        if entry.line_index is None:
            raise BackendError("Entry has no source line")
        text, source, source_type = self._read_source(entry)
        self.create_backup(source, source_type, text)
        self._write_text_source(source, source_type, replace_line(text, entry.line_index, None))

    def restore_backup(self, record: dict[str, object]) -> None:
        source = str(record["source"])
        source_type = str(record["source_type"])
        payload = Path(str(record["payload_path"]))
        if not payload.exists():
            raise BackendError("Backup payload is missing")
        try:
            self.create_backup(source, source_type)
        except BackendError:
            pass
        if bool(record.get("binary")):
            with tempfile.NamedTemporaryFile("wb", delete=False, prefix="prazycron-restore-") as tmp:
                tmp.write(payload.read_bytes())
                tmp_path = tmp.name
            mode = int(record.get("mode", 0o755))
            try:
                if os.access(Path(source).parent, os.W_OK):
                    shutil.copyfile(tmp_path, source)
                    os.chmod(source, mode)
                else:
                    args = ["install", "-m", f"{mode:04o}", tmp_path, source]
                    if os.geteuid() != 0:
                        args.insert(0, "pkexec")
                    self._run(args, timeout=90)
            finally:
                try: os.unlink(tmp_path)
                except OSError: pass
        else:
            self._write_text_source(source, source_type, payload.read_text(encoding="utf-8"))


    def preview_add(self, source: str, source_type: str, schedule: str, command: str, user: str, enabled: bool = True) -> tuple[str, str, str]:
        text, _, _ = self._read_source((source, source_type))
        system_format = source_type in {"system_file", "cron_d"}
        line = render_line(schedule, command, user, system_format, enabled)
        new_text = append_line(text, line)
        return text, new_text, self.unified_diff(text, new_text, source)

    def preview_edit(self, entry: CronEntry, schedule: str, command: str, user: str, enabled: bool) -> tuple[str, str, str]:
        if not entry.editable or entry.line_index is None:
            raise BackendError("This entry cannot be edited as a crontab line")
        text, source, source_type = self._read_source(entry)
        system_format = source_type in {"system_file", "cron_d"}
        line = render_line(schedule, command, user, system_format, enabled)
        new_text = replace_line(text, entry.line_index, line)
        return text, new_text, self.unified_diff(text, new_text, source)

    def preview_delete(self, entry: CronEntry) -> str:
        if entry.source_type == "directory_script":
            return f"--- {entry.source}\n+++ /dev/null\n- {entry.command}\n"
        if entry.line_index is None:
            raise BackendError("Entry has no source line")
        text, source, _source_type = self._read_source(entry)
        new_text = replace_line(text, entry.line_index, None)
        return self.unified_diff(text, new_text, source)

    @staticmethod
    def unified_diff(before: str, after: str, source: str) -> str:
        return "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=source + " (before)", tofile=source + " (after)", n=4,
        )) or "No textual changes."

    def source_environment(self, entry: CronEntry) -> dict[str, str]:
        return dict(parse_environment(self._read_source(entry)[0]))

    def update_source_environment(self, entry: CronEntry, values: dict[str, str]) -> str:
        if entry.source_type not in {"user_crontab", "root_crontab", "system_file", "cron_d"}:
            raise BackendError("This source does not support Cron environment variables")
        text, source, source_type = self._read_source(entry)
        new_text = update_environment(text, values)
        diff = self.unified_diff(text, new_text, source)
        self.create_backup(source, source_type, text)
        self._write_text_source(source, source_type, new_text)
        return diff

    def run_now(self, entry: CronEntry, timeout: int = 3600, entry_id: str | None = None) -> ExecutionRecord:
        command = entry.command
        current_user = getpass.getuser()
        if entry.user and entry.user != current_user:
            if entry.user == "root":
                command = shlex.join(["pkexec", "/bin/sh", "-c", entry.command])
            else:
                command = shlex.join(["pkexec", "--user", entry.user, "/bin/sh", "-c", entry.command])
        return run_command(command, entry_id or entry_signature(entry), timeout=timeout, mode="manual")

    @staticmethod
    def available_destinations(include_root: bool = False) -> list[tuple[str, str, str]]:
        result = [("User crontab", "crontab:user", "user_crontab")]
        if include_root:
            result.append(("Root crontab", "crontab:root", "root_crontab"))
        result.append(("/etc/crontab", "/etc/crontab", "system_file"))
        return result
