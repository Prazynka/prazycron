#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_one(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Could not update version in {path}")
    path.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Update PrazyCron release version.")
    parser.add_argument("version", help="Semantic version, for example 2.1.1")
    args = parser.parse_args()
    version = args.version.strip().lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise SystemExit(f"Invalid semantic version: {version}")

    current_text = (ROOT / "prazycron/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', current_text)
    if not match:
        raise SystemExit("Current version was not found")
    current = match.group(1)
    if current == version:
        print(f"Version is already {version}")
        return 0

    replace_one(ROOT / "prazycron/__init__.py", r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"')
    replace_one(ROOT / "pyproject.toml", r'^version\s*=\s*"[^"]+"', f'version = "{version}"')
    replace_one(ROOT / "build-deb.sh", r'^VERSION="[^"]+"', f'VERSION="{version}"')
    replace_one(ROOT / "CITATION.cff", r'^version:\s*.*$', f'version: {version}')
    replace_one(ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml", r'placeholder:\s*"[^"]+"', f'placeholder: "{version}"')

    for relative in ["README.md", "docs/INSTALLATION.md", "docs/GITHUB-PUBLISHING.md", "docs/RELEASING.md", "packaging/prazycron.1"]:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8").replace(current, version)
        path.write_text(text, encoding="utf-8")

    metainfo = ROOT / "packaging/net.prazynka.PrazyCron.metainfo.xml"
    text = metainfo.read_text(encoding="utf-8")
    today = dt.date.today().isoformat()
    text = text.replace("<releases>", f'<releases><release version="{version}" date="{today}"/>', 1)
    metainfo.write_text(text, encoding="utf-8")

    snap = ROOT / "snap/snapcraft.yaml"
    if snap.exists():
        replace_one(snap, r"^version:\s*['\"]?[^'\"\n]+['\"]?$", f"version: '{version}'")

    notes = ROOT / f"RELEASE-NOTES-{version}.md"
    if not notes.exists():
        notes.write_text(
            f"# PrazyCron {version}\n\n## Changes\n\n- Describe the changes in this release.\n",
            encoding="utf-8",
        )

    print(f"Updated version: {current} -> {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
