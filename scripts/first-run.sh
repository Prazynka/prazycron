#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL=0
NONINTERACTIVE=0
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    --non-interactive) NONINTERACTIVE=1 ;;
    -h|--help)
      cat <<'HELP'
Usage: ./scripts/first-run.sh [--install] [--non-interactive]

Checks publication dependencies. With --install, installs missing packages on
supported Linux distributions and starts GitHub CLI authentication if needed.
HELP
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1; }

install_debian() {
  sudo apt-get update
  sudo apt-get install -y \
    git gh python3 python3-tk python3-pil python3-pil.imagetk \
    xvfb cron util-linux systemd polkitd pkexec desktop-file-utils \
    appstream dpkg-dev zip unzip tar gzip coreutils curl ca-certificates
}

install_fedora() {
  sudo dnf install -y \
    git gh python3 python3-tkinter python3-pillow xorg-x11-server-Xvfb \
    cronie util-linux systemd polkit desktop-file-utils appstream \
    rpm-build zip unzip tar gzip coreutils curl ca-certificates
}

install_arch() {
  sudo pacman -Syu --needed --noconfirm \
    git github-cli python tk python-pillow xorg-server-xvfb cronie util-linux \
    systemd polkit desktop-file-utils appstream zip unzip tar gzip coreutils curl ca-certificates
}

install_opensuse() {
  sudo zypper --non-interactive install \
    git gh python3 python3-tk python3-Pillow xvfb-run cron util-linux systemd \
    polkit desktop-file-utils AppStream zip unzip tar gzip coreutils curl ca-certificates
}

missing=()
for cmd in git python3 tar gzip sha256sum; do
  need "$cmd" || missing+=("$cmd")
done
need gh || missing+=("gh")

if ((${#missing[@]})); then
  printf 'Missing tools: %s\n' "${missing[*]}"
  if ((INSTALL == 0)); then
    echo "Run: ./scripts/first-run.sh --install" >&2
    exit 1
  fi
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  else
    ID=unknown
    ID_LIKE=
  fi
  case "${ID:-unknown} ${ID_LIKE:-}" in
    *ubuntu*|*debian*|*mint*) install_debian ;;
    *fedora*|*rhel*) install_fedora ;;
    *arch*) install_arch ;;
    *suse*) install_opensuse ;;
    *)
      echo "Automatic dependency installation is not configured for this distribution." >&2
      exit 1
      ;;
  esac
fi

log "Checking GitHub authentication"
if ! gh auth status -h github.com >/dev/null 2>&1; then
  if ((NONINTERACTIVE)); then
    echo "GitHub CLI is not authenticated." >&2
    exit 1
  fi
  echo "A browser-based GitHub sign-in will open. This is the only required account confirmation."
  gh auth login --hostname github.com --git-protocol https --web
fi

gh auth setup-git >/dev/null 2>&1 || true

log "Checking project"
cd "$ROOT"
python3 - <<'PY'
from pathlib import Path
required = [
    'README.md', 'LICENSE', 'pyproject.toml', 'prazycron/__init__.py',
    '.github/workflows/ci.yml', '.github/workflows/release.yml',
    'scripts/publish.sh', 'scripts/make-release.sh'
]
missing = [p for p in required if not Path(p).exists()]
if missing:
    raise SystemExit('Missing project files: ' + ', '.join(missing))
print('Project files: OK')
PY

echo "GitHub publication prerequisites are ready."
