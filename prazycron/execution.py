from __future__ import annotations

import fcntl
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import DATA_DIR

HISTORY_DIR = DATA_DIR / "history"
LOCK_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"prazycron-locks-{os.getuid()}"


@dataclass(slots=True)
class ExecutionRecord:
    entry_id: str
    started: str
    finished: str
    duration_seconds: float
    exit_code: int
    command: str
    stdout: str
    stderr: str
    mode: str


def _history_file(entry_id: str, history_dir: Path = HISTORY_DIR) -> Path:
    safe = "".join(ch for ch in entry_id if ch.isalnum() or ch in "-_")[:80] or "unknown"
    return history_dir / f"{safe}.jsonl"


def append_history(record: ExecutionRecord, history_dir: Path = HISTORY_DIR, owner: str | None = None) -> None:
    history_dir.mkdir(parents=True, exist_ok=True)
    path = _history_file(record.entry_id, history_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    try:
        os.chmod(path, 0o600)
        if owner and os.geteuid() == 0:
            import pwd
            account = pwd.getpwnam(owner)
            os.chown(history_dir, account.pw_uid, account.pw_gid)
            os.chown(path, account.pw_uid, account.pw_gid)
    except (OSError, KeyError):
        pass


def read_history(entry_id: str, limit: int = 200, history_dir: Path = HISTORY_DIR) -> list[ExecutionRecord]:
    path = _history_file(entry_id, history_dir)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    records: list[ExecutionRecord] = []
    for line in reversed(lines):
        try:
            records.append(ExecutionRecord(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return records


def run_command(command: str, entry_id: str, *, timeout: int = 3600, mode: str = "manual", env: dict[str, str] | None = None, cwd: str | None = None, history_dir: Path = HISTORY_DIR, owner: str | None = None, record_history: bool = True) -> ExecutionRecord:
    started_dt = datetime.now(timezone.utc)
    start = time.monotonic()
    try:
        proc = subprocess.run(["/bin/sh", "-c", command], text=True, capture_output=True, timeout=timeout, check=False, env=env, cwd=cwd)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""; stderr = (exc.stderr or "") + f"\nPrazyCron: przekroczono limit {timeout} s."
        code = 124
    finished_dt = datetime.now(timezone.utc)
    record = ExecutionRecord(
        entry_id=entry_id, started=started_dt.isoformat(), finished=finished_dt.isoformat(),
        duration_seconds=round(time.monotonic() - start, 3), exit_code=code, command=command,
        stdout=stdout[-200_000:], stderr=stderr[-200_000:], mode=mode,
    )
    if record_history:
        append_history(record, history_dir=history_dir, owner=owner)
    return record


def runner_main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="prazycron-run")
    parser.add_argument("--id", default="")
    parser.add_argument("--lock", default="")
    parser.add_argument("--timeout", type=int, default=86400)
    parser.add_argument("--history-dir", default=str(HISTORY_DIR))
    parser.add_argument("--owner", default="")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command_parts = list(args.command)
    if command_parts and command_parts[0] == "--":
        command_parts.pop(0)
    if not command_parts:
        parser.error("missing command after --")
    command = shlex.join(command_parts)
    entry_id = args.id or args.lock or "scheduled"
    lock_handle = None
    if args.lock:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = LOCK_DIR / f"{args.lock}.lock"
        lock_handle = lock_path.open("w")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75
    record = run_command(command, entry_id, timeout=args.timeout, mode="scheduled", history_dir=Path(args.history_dir), owner=args.owner or None, record_history=not args.no_history)
    if record.stdout:
        sys.stdout.write(record.stdout)
    if record.stderr:
        sys.stderr.write(record.stderr)
    return record.exit_code


if __name__ == "__main__":
    raise SystemExit(runner_main())
