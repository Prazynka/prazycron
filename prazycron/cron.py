from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .model import CronEntry

DISABLED_PREFIX = "# PRAZYCRON_DISABLED "
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
ALIASES = {"@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"}


def split_cron_line(line: str, system_format: bool) -> tuple[str, str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or ENV_RE.match(stripped):
        return None
    if stripped.startswith("@"):
        parts = stripped.split(None, 2 if system_format else 1)
        if not parts or parts[0] not in ALIASES:
            return None
        if system_format:
            if len(parts) < 3:
                return None
            return parts[0], parts[2], parts[1]
        if len(parts) < 2:
            return None
        return parts[0], parts[1], ""
    parts = stripped.split()
    minimum = 7 if system_format else 6
    if len(parts) < minimum:
        return None
    schedule = " ".join(parts[:5])
    if system_format:
        return schedule, " ".join(parts[6:]), parts[5]
    return schedule, " ".join(parts[5:]), ""


def parse_crontab_text(text: str, source: str, source_type: str, default_user: str) -> list[CronEntry]:
    entries: list[CronEntry] = []
    system_format = source_type in {"system_file", "cron_d"}
    for index, original in enumerate(text.splitlines()):
        raw = original
        enabled = True
        candidate = original
        stripped = original.lstrip()
        if stripped.startswith(DISABLED_PREFIX):
            enabled = False
            indent = original[: len(original) - len(stripped)]
            candidate = indent + stripped[len(DISABLED_PREFIX):]
        parsed = split_cron_line(candidate, system_format)
        if not parsed:
            continue
        schedule, command, user = parsed
        entries.append(CronEntry(
            enabled=enabled,
            schedule=schedule,
            command=command,
            user=user or default_user,
            source=source,
            source_type=source_type,
            line_index=index,
            raw=raw,
        ))
    return entries


def parse_directory(directory: str, schedule: str) -> list[CronEntry]:
    path = Path(directory)
    if not path.is_dir():
        return []
    entries: list[CronEntry] = []
    try:
        children = sorted(path.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []
    for child in children:
        if not child.is_file() or child.name.startswith("."):
            continue
        try:
            enabled = bool(child.stat().st_mode & 0o111)
        except OSError:
            enabled = False
        entries.append(CronEntry(
            enabled=enabled,
            schedule=schedule,
            command=str(child),
            user="root",
            source=str(child),
            source_type="directory_script",
            line_index=None,
            raw=str(child),
            metadata={"directory": directory},
        ))
    return entries


def render_line(schedule: str, command: str, user: str, system_format: bool, enabled: bool = True) -> str:
    schedule = schedule.strip()
    command = command.strip()
    if not schedule or not command:
        raise ValueError("Schedule and command are required")
    if schedule.startswith("@"):
        if schedule not in ALIASES:
            raise ValueError("Unknown schedule alias")
    elif len(schedule.split()) != 5:
        raise ValueError("Cron schedule must have five fields")
    line = f"{schedule} {user.strip()} {command}" if system_format else f"{schedule} {command}"
    return line if enabled else DISABLED_PREFIX + line


def replace_line(text: str, line_index: int, new_line: str | None) -> str:
    lines = text.splitlines()
    if line_index < 0 or line_index >= len(lines):
        raise IndexError("Cron line no longer exists")
    if new_line is None:
        del lines[line_index]
    else:
        lines[line_index] = new_line
    return "\n".join(lines) + ("\n" if lines else "")


def append_line(text: str, line: str) -> str:
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line.rstrip("\n") + "\n"


def toggle_line(line: str, enabled: bool) -> str:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if enabled:
        if stripped.startswith(DISABLED_PREFIX):
            return indent + stripped[len(DISABLED_PREFIX):]
        return line
    if stripped.startswith(DISABLED_PREFIX):
        return line
    return indent + DISABLED_PREFIX + stripped
