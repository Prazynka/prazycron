from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import CronEntry
from .schedule import next_runs


@dataclass(slots=True)
class Conflict:
    kind: str
    left_key: str
    right_key: str
    message: str


def normalized_command(command: str) -> str:
    return " ".join(command.strip().split())


def lock_id(entry: CronEntry) -> str:
    return hashlib.sha256(f"{entry.source}|{entry.user}|{normalized_command(entry.command)}".encode()).hexdigest()[:16]


def wrap_with_flock(command: str, identifier: str) -> str:
    if "prazycron-run" in command and "--lock" in command:
        return command
    return f"/usr/bin/prazycron-run --lock {shlex.quote(identifier)} -- /bin/sh -c {shlex.quote(command)}"


def unwrap_prazycron(command: str) -> str:
    marker = " -- /bin/sh -c "
    if command.startswith("/usr/bin/prazycron-run ") and marker in command:
        raw = command.split(marker, 1)[1]
        try:
            parsed = shlex.split(raw)
            return parsed[0] if parsed else command
        except ValueError:
            return command
    return command


def detect_conflicts(entries: list[CronEntry], horizon_days: int = 14) -> list[Conflict]:
    conflicts: list[Conflict] = []
    cache: dict[str, set[datetime]] = {}
    for entry in entries:
        if not entry.enabled or entry.schedule == "@reboot":
            continue
        try:
            cache[entry.key] = set(next_runs(entry.schedule, 200, max_days=horizon_days))
        except ValueError:
            cache[entry.key] = set()
    for index, left in enumerate(entries):
        if not left.enabled:
            continue
        for right in entries[index + 1:]:
            if not right.enabled:
                continue
            same_command = normalized_command(unwrap_prazycron(left.command)) == normalized_command(unwrap_prazycron(right.command))
            simultaneous = bool(cache.get(left.key, set()) & cache.get(right.key, set()))
            if same_command:
                conflicts.append(Conflict("duplicate", left.key, right.key, f"To samo polecenie występuje w dwóch zadaniach: {left.source_name} i {right.source_name}."))
            elif simultaneous:
                conflicts.append(Conflict("simultaneous", left.key, right.key, f"Zadania mogą uruchomić się jednocześnie: {left.source_name} oraz {right.source_name}."))
    return conflicts
