from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .model import CronEntry

METADATA_FILE = DATA_DIR / "entry-metadata.json"


def entry_signature(entry: CronEntry) -> str:
    payload = "\0".join((entry.source_type, entry.source, entry.user, entry.schedule, entry.command))
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


class MetadataStore:
    """Small local database for labels and UI-only properties.

    Cron files remain valid, portable text files. Metadata is deliberately kept outside
    them, so PrazyCron never injects private comments into files managed by packages.
    """

    def __init__(self, path: Path = METADATA_FILE) -> None:
        self.path = path
        self.data: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = raw if isinstance(raw, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def get(self, entry: CronEntry) -> dict[str, Any]:
        return dict(self.data.get(entry_signature(entry), {}))

    def update(self, entry: CronEntry, **values: Any) -> None:
        key = entry_signature(entry)
        record = dict(self.data.get(key, {}))
        for name, value in values.items():
            if value in (None, "", [], {}):
                record.pop(name, None)
            else:
                record[name] = value
        if record:
            self.data[key] = record
        else:
            self.data.pop(key, None)
        self.save()

    def migrate(self, old_entry: CronEntry, new_entry: CronEntry) -> None:
        old_key = entry_signature(old_entry)
        record = self.data.pop(old_key, None)
        if record:
            self.data[entry_signature(new_entry)] = record
            self.save()

    def remove(self, entry: CronEntry) -> None:
        if self.data.pop(entry_signature(entry), None) is not None:
            self.save()
