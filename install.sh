#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run this installer with sudo:" >&2
  echo "sudo ./install.sh" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_DIR="/usr/lib/python3/dist-packages/prazycron"

install -d "$PYTHON_DIR"
install -m 0644 "$ROOT_DIR"/prazycron/*.py "$PYTHON_DIR"/
install -Dm 0755 /dev/stdin /usr/bin/prazycron <<'EOF'
#!/usr/bin/env bash
exec python3 -m prazycron.main "$@"
EOF
install -Dm 0755 /dev/stdin /usr/bin/prazycron-run <<'EOF'
#!/usr/bin/env bash
exec python3 -m prazycron.execution "$@"
EOF
install -Dm 0644 "$ROOT_DIR/packaging/prazycron.desktop" /usr/share/applications/prazycron.desktop
install -Dm 0644 "$ROOT_DIR/packaging/net.prazynka.PrazyCron.metainfo.xml" /usr/share/metainfo/net.prazynka.PrazyCron.metainfo.xml
for size in 16 24 32 48 64 128 256 512; do
  install -Dm 0644 "$ROOT_DIR/assets/prazycron-${size}.png" "/usr/share/icons/hicolor/${size}x${size}/apps/prazycron.png"
done
install -Dm 0644 "$ROOT_DIR/assets/status-on.png" /usr/share/prazycron/status-on.png
install -Dm 0644 "$ROOT_DIR/assets/status-off.png" /usr/share/prazycron/status-off.png
install -Dm 0644 "$ROOT_DIR/README.md" /usr/share/doc/prazycron/README.md
install -Dm 0644 "$ROOT_DIR/LICENSE" /usr/share/doc/prazycron/copyright
mkdir -p /usr/share/man/man1
gzip -c "$ROOT_DIR/packaging/prazycron.1" > /usr/share/man/man1/prazycron.1.gz
chmod 0644 /usr/share/man/man1/prazycron.1.gz
command -v update-desktop-database >/dev/null && update-desktop-database /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true

echo "PrazyCron installed. Run: prazycron"
