from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from .model import CronEntry

USER_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEM_DIR = Path("/etc/systemd/system")


def _run(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def _read_unit(name: str, user: bool) -> str:
    path = (USER_DIR if user else SYSTEM_DIR) / name
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        args = ["systemctl", "--user", "cat", name] if user else ["systemctl", "cat", name]
        proc = _run(args)
        return proc.stdout if proc.returncode == 0 else ""


def _unit_property(text: str, section: str, key: str) -> str:
    current = ""
    values: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == section and line.startswith(key + "="):
            values.append(line.split("=", 1)[1])
    return ", ".join(values)


def list_timers(include_system: bool = True) -> list[CronEntry]:
    entries: list[CronEntry] = []
    scopes = [(True, ["systemctl", "--user", "list-timers", "--all", "--no-legend", "--no-pager"])]
    if include_system:
        scopes.append((False, ["systemctl", "list-timers", "--all", "--no-legend", "--no-pager"]))
    for user_scope, args in scopes:
        try:
            proc = _run(args)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            parts = line.split()
            timer_name = next((part for part in parts if part.endswith(".timer")), "")
            if not timer_name:
                continue
            timer_text = _read_unit(timer_name, user_scope)
            service_name = _unit_property(timer_text, "Timer", "Unit") or timer_name[:-6] + ".service"
            service_text = _read_unit(service_name, user_scope)
            schedule = _unit_property(timer_text, "Timer", "OnCalendar") or _unit_property(timer_text, "Timer", "OnBootSec") or "systemd timer"
            command = _unit_property(service_text, "Service", "ExecStart") or service_name
            enabled_proc = _run(["systemctl", "--user", "is-enabled", timer_name] if user_scope else ["systemctl", "is-enabled", timer_name])
            enabled = enabled_proc.returncode == 0
            entries.append(CronEntry(
                enabled=enabled, schedule=schedule, command=command, user=os.environ.get("USER", "user") if user_scope else "root",
                source=timer_name, source_type="systemd_user_timer" if user_scope else "systemd_system_timer",
                line_index=None, raw=timer_text + "\n\n" + service_text,
                metadata={"timer_unit": timer_name, "service_unit": service_name, "scope": "user" if user_scope else "system", "next": line.split(timer_name, 1)[0].strip(), "managed": timer_name.startswith("prazycron-") and ((USER_DIR if user_scope else SYSTEM_DIR) / timer_name).exists()},
            ))
    return entries


def create_timer(name: str, on_calendar: str, command: str, description: str = "PrazyCron managed task", *, user_scope: bool = True, persistent: bool = True, random_delay: str = "") -> tuple[Path, Path]:
    if not on_calendar.strip():
        raise ValueError("OnCalendar cannot be empty")
    if not command.strip():
        raise ValueError("Command cannot be empty")
    calendar_check = _run(["systemd-analyze", "calendar", on_calendar.strip()])
    if calendar_check.returncode != 0:
        raise ValueError((calendar_check.stderr or calendar_check.stdout or "Invalid OnCalendar expression").strip())
    safe = re.sub(r"[^A-Za-z0-9_.@-]+", "-", name.strip()).strip("-.") or "prazycron-task"
    if not safe.startswith("prazycron-"):
        safe = "prazycron-" + safe
    service_name = safe + ".service"
    timer_name = safe + ".timer"
    service = f"""[Unit]\nDescription={description}\n\n[Service]\nType=oneshot\nExecStart=/bin/sh -c {shlex.quote(command)}\n"""
    timer = f"""[Unit]\nDescription={description}\n\n[Timer]\nOnCalendar={on_calendar}\nPersistent={'true' if persistent else 'false'}\n"""
    if random_delay.strip():
        timer += f"RandomizedDelaySec={random_delay.strip()}\n"
    timer += f"Unit={service_name}\n\n[Install]\nWantedBy=timers.target\n"
    target = USER_DIR if user_scope else SYSTEM_DIR
    target.mkdir(parents=True, exist_ok=True) if user_scope else None
    service_path, timer_path = target / service_name, target / timer_name
    if user_scope:
        service_path.write_text(service, encoding="utf-8"); timer_path.write_text(timer, encoding="utf-8")
        reload_proc = _run(["systemctl", "--user", "daemon-reload"])
        if reload_proc.returncode != 0:
            raise RuntimeError(reload_proc.stderr or reload_proc.stdout or "systemctl --user daemon-reload failed")
        enable_proc = _run(["systemctl", "--user", "enable", "--now", timer_name])
        if enable_proc.returncode != 0:
            raise RuntimeError(enable_proc.stderr or enable_proc.stdout or "Unable to enable the user timer")
    else:
        with tempfile.TemporaryDirectory(prefix="prazycron-systemd-") as temp:
            sp = Path(temp) / service_name; tp = Path(temp) / timer_name
            sp.write_text(service, encoding="utf-8"); tp.write_text(timer, encoding="utf-8")
            proc = _run(["pkexec", "install", "-m", "0644", str(sp), str(service_path)], timeout=90)
            if proc.returncode != 0: raise RuntimeError(proc.stderr or "Nie udało się zapisać jednostki systemowej.")
            proc = _run(["pkexec", "install", "-m", "0644", str(tp), str(timer_path)], timeout=90)
            if proc.returncode != 0: raise RuntimeError(proc.stderr or "Nie udało się zapisać timera systemowego.")
            reload_proc = _run(["pkexec", "systemctl", "daemon-reload"], timeout=90)
            if reload_proc.returncode != 0: raise RuntimeError(reload_proc.stderr or "systemctl daemon-reload failed")
            enable_proc = _run(["pkexec", "systemctl", "enable", "--now", timer_name], timeout=90)
            if enable_proc.returncode != 0: raise RuntimeError(enable_proc.stderr or "Unable to enable the system timer")
    return service_path, timer_path


def toggle_timer(entry: CronEntry) -> None:
    unit = entry.metadata.get("timer_unit", entry.source)
    user_scope = entry.source_type == "systemd_user_timer"
    action = "disable" if entry.enabled else "enable"
    args = ["systemctl", "--user", action, "--now", unit] if user_scope else ["pkexec", "systemctl", action, "--now", unit]
    proc = _run(args, timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "Nie udało się zmienić stanu timera.")


def delete_timer(entry: CronEntry) -> None:
    if not bool(entry.metadata.get("managed", False)):
        raise RuntimeError("For safety, PrazyCron deletes only timers created by PrazyCron in ~/.config/systemd/user or /etc/systemd/system.")
    unit = entry.metadata.get("timer_unit", entry.source)
    service = entry.metadata.get("service_unit", unit[:-6] + ".service")
    user_scope = entry.source_type == "systemd_user_timer"
    base = USER_DIR if user_scope else SYSTEM_DIR
    if user_scope:
        _run(["systemctl", "--user", "disable", "--now", unit])
        for name in (unit, service):
            try: (base / name).unlink()
            except FileNotFoundError: pass
        _run(["systemctl", "--user", "daemon-reload"])
    else:
        _run(["pkexec", "systemctl", "disable", "--now", unit], timeout=90)
        _run(["pkexec", "rm", "-f", str(base / unit), str(base / service)], timeout=90)
        _run(["pkexec", "systemctl", "daemon-reload"], timeout=90)
