from __future__ import annotations

import re
from collections import OrderedDict

ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
KNOWN_ENV = ("SHELL", "PATH", "HOME", "MAILTO", "CRON_TZ")


def parse_environment(text: str) -> OrderedDict[str, str]:
    result: OrderedDict[str, str] = OrderedDict()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ENV_LINE_RE.match(line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def update_environment(text: str, values: dict[str, str]) -> str:
    """Update source-level environment variables while preserving comments and jobs."""
    normalized = {str(k).strip(): str(v).strip() for k, v in values.items() if str(k).strip()}
    lines = text.splitlines()
    seen: set[str] = set()
    output: list[str] = []
    insert_after = 0
    for idx, line in enumerate(lines):
        match = ENV_LINE_RE.match(line)
        if match:
            name = match.group(1)
            seen.add(name)
            if name in normalized and normalized[name] != "":
                output.append(f"{name}={normalized[name]}")
            # Empty values remove the variable.
            insert_after = len(output)
        else:
            output.append(line)
            if not line.strip() or line.lstrip().startswith("#"):
                insert_after = len(output)
    missing = [f"{name}={value}" for name, value in normalized.items() if name not in seen and value != ""]
    if missing:
        output[insert_after:insert_after] = missing
    return "\n".join(output) + ("\n" if output else "")
