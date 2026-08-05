from __future__ import annotations

import json
import locale
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "prazycron"
CONFIG_FILE = CONFIG_DIR / "settings.json"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "prazycron"
BACKUP_DIR = DATA_DIR / "backups"

DEFAULTS: dict[str, Any] = {
    "language": "en",
    "language_explicit": False,
    "theme": "dark",
    "font_family": "Noto Sans",
    "font_size": 11,
    "row_height": 40,
    "colors": {
        "window": "#171b21",
        "panel": "#1d232b",
        "field": "#232a33",
        "header": "#303844",
        "row_even": "#20262e",
        "row_odd": "#252c35",
        "selected": "#3478c9",
        "text": "#eef2f6",
        "muted": "#aeb8c5",
        "accent": "#3b82d0",
        "grid": "#46505e",
        "on": "#57b75f",
        "off": "#818995",
    },
    "provider": "builtin",
    "ollama_endpoint": "http://127.0.0.1:11434/api/generate",
    "ollama_model": "qwen2.5:3b",
    "openai_endpoint": "https://api.openai.com/v1/responses",
    "openai_model": "gpt-5-mini",
    "confirm_destructive": True,
    "load_root_on_start": False,
    "read_only": False,
    "password_record": None,
    "visible_columns": ["state", "favorite", "name", "schedule", "user", "source", "last_run", "next_run", "command"],
    "show_system_timers": True,
    "history_limit": 200,
    "run_timeout": 3600,
    "confirm_run_now": True,
}

THEMES: dict[str, dict[str, str]] = {
    "dark": DEFAULTS["colors"].copy(),
    "light": {
        "window": "#eef1f5", "panel": "#ffffff", "field": "#ffffff", "header": "#dbe2ea",
        "row_even": "#ffffff", "row_odd": "#f3f6f9", "selected": "#3b82d0", "text": "#17202b",
        "muted": "#647182", "accent": "#2569ad", "grid": "#a9b4c0", "on": "#258a37", "off": "#7a838e",
    },
    "blue": {
        "window": "#0f1d2b", "panel": "#14283b", "field": "#193249", "header": "#244660",
        "row_even": "#13283b", "row_odd": "#173047", "selected": "#1676c4", "text": "#ecf6ff",
        "muted": "#a7c2d7", "accent": "#35a4f4", "grid": "#315a74", "on": "#65c66b", "off": "#88a2b5",
    },
    "green": {
        "window": "#132019", "panel": "#192a20", "field": "#21352a", "header": "#2d4738",
        "row_even": "#192a20", "row_odd": "#1e3126", "selected": "#347e53", "text": "#eff9f2",
        "muted": "#b2c7b8", "accent": "#50aa70", "grid": "#466451", "on": "#72d27c", "off": "#86968a",
    },
    "high_contrast": {
        "window": "#000000", "panel": "#000000", "field": "#101010", "header": "#242424",
        "row_even": "#000000", "row_odd": "#101010", "selected": "#005fcc", "text": "#ffffff",
        "muted": "#d5d5d5", "accent": "#00b7ff", "grid": "#ffffff", "on": "#5dff6b", "off": "#d0d0d0",
    },
}


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def load_config() -> dict[str, Any]:
    cfg = _deep_copy(DEFAULTS)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        for key, value in data.items():
            if key == "colors" and isinstance(value, dict):
                cfg["colors"].update(value)
            else:
                cfg[key] = value
        # The product default is English. Preserve a language only when the user
        # explicitly selected it; legacy automatically selected values migrate to
        # the current default. This also upgrades the temporary Polish default used
        # by version 2.0.1 without overriding a deliberate language choice.
        if not bool(data.get("language_explicit", False)):
            cfg["language"] = "en"
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def apply_theme(cfg: dict[str, Any], theme: str) -> None:
    if theme in THEMES:
        cfg["theme"] = theme
        cfg["colors"] = THEMES[theme].copy()
