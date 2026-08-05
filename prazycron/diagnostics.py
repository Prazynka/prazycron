from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .backend import CronBackend
from .model import CronEntry
from .overlap import detect_conflicts
from .validation import validate_cron_entry


@dataclass(slots=True)
class DiagnosticItem:
    severity: str
    subject: str
    message: str


def _source_checks(source: str, source_type: str) -> list[DiagnosticItem]:
    if source_type in {"user_crontab", "root_crontab"}:
        return []
    path = Path(source)
    items: list[DiagnosticItem] = []
    if not path.exists():
        return [DiagnosticItem("error", source, "Źródło nie istnieje.")]
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if source_type in {"system_file", "cron_d"} and mode & 0o022:
            items.append(DiagnosticItem("error", source, f"Plik jest zapisywalny przez grupę lub innych użytkowników ({mode:04o})."))
        if source_type == "directory_script":
            if not mode & 0o111:
                items.append(DiagnosticItem("warning", source, "Skrypt nie ma prawa wykonywania."))
            try:
                first = path.open("r", encoding="utf-8", errors="replace").readline().strip()
                if not first.startswith("#!"):
                    items.append(DiagnosticItem("warning", source, "Skrypt nie rozpoczyna się od shebang #!."))
                elif not Path(first[2:].split()[0]).exists():
                    items.append(DiagnosticItem("error", source, f"Interpreter z shebang nie istnieje: {first[2:]}."))
            except OSError:
                pass
        if path.is_file():
            data = path.read_bytes()
            if data and not data.endswith(b"\n"):
                items.append(DiagnosticItem("warning", source, "Plik nie kończy się znakiem nowej linii."))
    except OSError as exc:
        items.append(DiagnosticItem("error", source, str(exc)))
    return items


def diagnose(backend: CronBackend, entries: list[CronEntry]) -> list[DiagnosticItem]:
    items: list[DiagnosticItem] = []
    items.append(DiagnosticItem("ok" if backend.service_running() else "error", "cron.service", "Usługa Cron działa." if backend.service_running() else "Usługa Cron nie działa."))
    seen_sources: set[tuple[str, str]] = set()
    for entry in entries:
        for issue in validate_cron_entry(entry):
            items.append(DiagnosticItem(issue.severity, entry.source_name, issue.message))
        key = (entry.source, entry.source_type)
        if key not in seen_sources:
            seen_sources.add(key)
            items.extend(_source_checks(*key))
        for path_text in re.findall(r"(?<![\w-])(/(?:[^\s'\";&|]+))", entry.command):
            path = Path(path_text.rstrip(",:)"))
            if any(ch in str(path) for ch in "*$?"):
                continue
            parent = path if path.is_dir() else path.parent
            if str(parent) not in {"/", "."} and not parent.exists():
                items.append(DiagnosticItem("warning", entry.source_name, f"Katalog użyty przez polecenie nie istnieje: {parent}"))
    for conflict in detect_conflicts(entries):
        items.append(DiagnosticItem("warning", "Konflikt harmonogramu", conflict.message))
    if not any(item.severity in {"error", "warning"} for item in items):
        items.append(DiagnosticItem("ok", "Podsumowanie", "Nie wykryto problemów."))
    return items
