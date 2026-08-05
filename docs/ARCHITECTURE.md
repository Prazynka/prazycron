# Architecture

PrazyCron is a Python application with two front ends and shared scheduling modules.

- `prazycron/gui.py` — Tk graphical interface.
- `prazycron/tui.py` — curses terminal interface.
- `prazycron/backend.py` — discovery and modification of Cron sources and backups.
- `prazycron/cron.py` — parsing and rendering of crontab lines.
- `prazycron/systemd_timers.py` — systemd timer discovery and management.
- `prazycron/validation.py` — pre-save checks.
- `prazycron/schedule.py` — future-run calculation and timezone handling.
- `prazycron/execution.py` — run-now and execution history.
- `prazycron/diagnostics.py` and `overlap.py` — health checks and conflict detection.
- `prazycron/i18n.py` — translation catalogs with English fallback.

Privileged changes are delegated to standard system tools through PolicyKit. The GUI and TUI use the same configuration and metadata stores.
