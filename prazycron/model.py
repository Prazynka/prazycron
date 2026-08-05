from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class CronEntry:
    enabled: bool
    schedule: str
    command: str
    user: str
    source: str
    source_type: str
    line_index: Optional[int]
    raw: str
    description: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        payload = f"{self.source_type}|{self.source}|{self.line_index}|{self.raw}"
        return sha1(payload.encode("utf-8", "replace")).hexdigest()

    @property
    def source_name(self) -> str:
        if self.source_type == "user_crontab":
            return "User crontab"
        if self.source_type == "root_crontab":
            return "Root crontab"
        return Path(self.source).name or self.source

    @property
    def editable(self) -> bool:
        return self.source_type in {"user_crontab", "root_crontab", "system_file", "cron_d"}

    @property
    def is_script(self) -> bool:
        return self.source_type == "directory_script"
