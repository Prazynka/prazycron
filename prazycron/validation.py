from __future__ import annotations

import getpass
import os
import pwd
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .analyzer import validate_schedule
from .model import CronEntry


@dataclass(slots=True)
class ValidationIssue:
    severity: str  # error | warning | info
    code: str
    message: str


def _first_executable(command: str) -> str:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return ""
    while tokens and ("=" in tokens[0] and not tokens[0].startswith(("/", "./"))):
        tokens.pop(0)
    while tokens and tokens[0] in {"env", "/usr/bin/env", "nice", "/usr/bin/nice", "nohup", "/usr/bin/nohup", "flock", "/usr/bin/flock"}:
        tokens.pop(0)
        while tokens and tokens[0].startswith("-"):
            tokens.pop(0)
        if tokens and "flock" in command and tokens[0].endswith(".lock"):
            tokens.pop(0)
    return tokens[0] if tokens else ""


def validate_entry(schedule: str, command: str, user: str, *, source_type: str = "user_crontab") -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for error in validate_schedule(schedule):
        issues.append(ValidationIssue("error", "schedule", error))
    if not command.strip():
        issues.append(ValidationIssue("error", "command-empty", "Polecenie nie może być puste."))
        return issues
    if not user.strip():
        issues.append(ValidationIssue("error", "user-empty", "Użytkownik nie może być pusty."))
    else:
        try:
            pwd.getpwnam(user.strip())
        except KeyError:
            issues.append(ValidationIssue("error", "user-missing", f"Użytkownik „{user}” nie istnieje w systemie."))
    try:
        shlex.split(command, posix=True)
    except ValueError as exc:
        issues.append(ValidationIssue("error", "quotes", f"Niepoprawne cudzysłowy lub znaki ucieczki: {exc}"))
    try:
        proc = subprocess.run(["/bin/bash", "-n", "-c", command], text=True, capture_output=True, timeout=5, check=False)
        if proc.returncode != 0:
            issues.append(ValidationIssue("error", "shell-syntax", (proc.stderr or "Błąd składni powłoki.").strip()))
    except (OSError, subprocess.TimeoutExpired):
        pass
    executable = _first_executable(command)
    shell_builtins = {"cd", "echo", "printf", "test", "[", "if", "for", "while", "case", "export", "source", ".", "true", "false"}
    if executable and executable not in shell_builtins and not executable.startswith(("$", "`", "(")):
        if "/" in executable:
            path = Path(os.path.expandvars(os.path.expanduser(executable)))
            if not path.exists():
                issues.append(ValidationIssue("warning", "executable-missing", f"Plik wykonywalny „{path}” nie istnieje."))
            elif path.is_file() and not os.access(path, os.X_OK):
                issues.append(ValidationIssue("warning", "not-executable", f"Plik „{path}” istnieje, ale nie ma prawa wykonywania."))
        elif shutil.which(executable) is None:
            issues.append(ValidationIssue("warning", "path-missing", f"Programu „{executable}” nie znaleziono w bieżącej zmiennej PATH. Cron może mieć jeszcze krótszy PATH."))
    if command.count("'") % 2 or command.count('"') % 2:
        issues.append(ValidationIssue("error", "quotes-count", "Liczba znaków cudzysłowu wygląda na nieparzystą."))
    if "%" in command and "\\%" not in command:
        issues.append(ValidationIssue("warning", "percent", "Znak % ma specjalne znaczenie w crontab; użyj \\%, jeśli ma trafić do polecenia."))
    risky = [
        (r"\brm\s+-rf\s+/(?:\s|$)", "Polecenie może usunąć główny system plików."),
        (r"(?:curl|wget).*\|\s*(?:sh|bash)", "Kod pobrany z sieci jest przekazywany bezpośrednio do powłoki."),
        (r"\bchmod\s+777\b", "Uprawnienia 777 są zwykle zbyt szerokie."),
        (r"\bsudo\b", "sudo w zadaniu Cron może oczekiwać hasła i zakończyć się błędem."),
    ]
    for pattern, message in risky:
        if re.search(pattern, command, re.I):
            issues.append(ValidationIssue("warning", "danger", message))
    if source_type == "user_crontab" and user not in {getpass.getuser(), ""}:
        issues.append(ValidationIssue("warning", "user-ignored", "Crontab użytkownika zawsze działa jako właściciel crontab; pole użytkownika nie jest zapisywane."))
    return issues


def validate_cron_entry(entry: CronEntry) -> list[ValidationIssue]:
    return validate_entry(entry.schedule, entry.command, entry.user, source_type=entry.source_type)
