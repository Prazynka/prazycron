from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from . import APP_FULL_NAME, APP_TAGLINE, __version__
from .analyzer import analyze, humanize_schedule
from .backend import CronBackend
from .config import load_config
from .diagnostics import diagnose
from .execution import read_history
from .metadata import MetadataStore, entry_signature
from .schedule import next_runs, system_timezone_name
from .systemd_timers import list_timers


def gui_available() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prazycron", description=f"{APP_FULL_NAME} — {APP_TAGLINE}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="force the graphical interface")
    mode.add_argument("--tui", action="store_true", help="force the terminal interface")
    parser.add_argument("--list", action="store_true", help="print cron tasks as a table and exit")
    parser.add_argument("--json", action="store_true", help="print cron tasks as JSON and exit")
    parser.add_argument("--analyze", metavar="INDEX", type=int, help="analyze one task by 1-based index")
    parser.add_argument("--next", metavar="INDEX", type=int, help="print the next ten runs for a task")
    parser.add_argument("--history", metavar="INDEX", type=int, help="print execution history for a task")
    parser.add_argument("--diagnose", action="store_true", help="run Cron diagnostics and exit")
    parser.add_argument("--systemd-list", action="store_true", help="print systemd timers and exit")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def noninteractive(args: argparse.Namespace) -> int:
    backend = CronBackend()
    entries = list_timers(include_system=True) if args.systemd_list else backend.load()
    if args.diagnose:
        items = diagnose(backend, backend.load())
        for item in items:
            print(f"[{item.severity.upper()}] {item.subject}: {item.message}")
        return 1 if any(item.severity == "error" for item in items) else 0
    if args.json:
        print(json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False)); return 0
    if args.analyze is not None:
        index=args.analyze-1
        if not 0 <= index < len(entries):
            print(f"No task number {args.analyze}", file=sys.stderr); return 2
        print(analyze(entries[index]).as_text()); return 0
    if args.next is not None:
        index=args.next-1
        if not 0 <= index < len(entries):
            print(f"No task number {args.next}", file=sys.stderr); return 2
        entry = entries[index]
        if entry.source_type.startswith("systemd_"):
            print(entry.schedule); return 0
        store = MetadataStore(); meta = store.get(entry); zone = str(meta.get("timezone") or system_timezone_name())
        for run in next_runs(entry.schedule, 10, timezone_name=zone):
            print(run.isoformat())
        return 0
    if args.history is not None:
        index=args.history-1
        if not 0 <= index < len(entries):
            print(f"No task number {args.history}", file=sys.stderr); return 2
        entry = entries[index]; meta = MetadataStore().get(entry); hid = str(meta.get("entry_id") or entry_signature(entry))
        for record in read_history(hid):
            print(f"{record.finished} code={record.exit_code} duration={record.duration_seconds:.3f}s {record.mode}")
        return 0
    language = str(load_config().get("language", "en"))
    print(f"{'#':>3} {'STATE':<5} {'SCHEDULE':<38} {'USER':<12} {'SOURCE':<26} COMMAND")
    for i,e in enumerate(entries,1):
        source=e.source if len(e.source)<=26 else '…'+e.source[-25:]
        schedule = e.schedule if e.source_type.startswith("systemd_") else humanize_schedule(e.schedule, language)
        print(f"{i:>3} {('ON' if e.enabled else 'OFF'):<5} {schedule:<38.38} {e.user:<12.12} {source:<26.26} {e.command}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list or args.json or args.analyze is not None or args.next is not None or args.history is not None or args.diagnose or args.systemd_list:
        return noninteractive(args)
    use_gui = args.gui or (not args.tui and gui_available())
    if use_gui:
        try:
            from .gui import run_gui
            return run_gui(smoke_test=args.smoke_test)
        except Exception as exc:
            if args.gui or not sys.stdin.isatty():
                print(f"Unable to start GUI: {exc}", file=sys.stderr); return 1
            print(f"GUI unavailable ({exc}); starting PrazyCron TUI.", file=sys.stderr)
    from .tui import run_tui
    return run_tui()


if __name__ == "__main__":
    raise SystemExit(main())
