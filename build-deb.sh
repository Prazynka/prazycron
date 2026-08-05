#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="2.1.0"
BUILD_DIR="${1:-$ROOT_DIR/build/deb-root}"
OUT_DIR="${2:-$ROOT_DIR/dist}"
PKG="$OUT_DIR/prazycron_${VERSION}_all.deb"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/DEBIAN" "$OUT_DIR"
mkdir -p "$BUILD_DIR/usr/lib/python3/dist-packages/prazycron"
install -m 0644 "$ROOT_DIR"/prazycron/*.py "$BUILD_DIR/usr/lib/python3/dist-packages/prazycron/"

install -Dm 0755 /dev/stdin "$BUILD_DIR/usr/bin/prazycron" <<'EOF'
#!/usr/bin/env bash
exec python3 -m prazycron.main "$@"
EOF

install -Dm 0755 /dev/stdin "$BUILD_DIR/usr/bin/prazycron-run" <<'EOF'
#!/usr/bin/env bash
exec python3 -m prazycron.execution "$@"
EOF

install -Dm 0644 "$ROOT_DIR/packaging/prazycron.desktop" "$BUILD_DIR/usr/share/applications/prazycron.desktop"
install -Dm 0644 "$ROOT_DIR/packaging/net.prazynka.PrazyCron.metainfo.xml" "$BUILD_DIR/usr/share/metainfo/net.prazynka.PrazyCron.metainfo.xml"
for size in 16 24 32 48 64 128 256 512; do
  install -Dm 0644 "$ROOT_DIR/assets/prazycron-${size}.png" "$BUILD_DIR/usr/share/icons/hicolor/${size}x${size}/apps/prazycron.png"
done
install -Dm 0644 "$ROOT_DIR/assets/status-on.png" "$BUILD_DIR/usr/share/prazycron/status-on.png"
install -Dm 0644 "$ROOT_DIR/assets/status-off.png" "$BUILD_DIR/usr/share/prazycron/status-off.png"
install -Dm 0644 "$ROOT_DIR/README.md" "$BUILD_DIR/usr/share/doc/prazycron/README.md"
install -Dm 0644 "$ROOT_DIR/LICENSE" "$BUILD_DIR/usr/share/doc/prazycron/copyright"
mkdir -p "$BUILD_DIR/usr/share/man/man1"
gzip -9c "$ROOT_DIR/packaging/prazycron.1" > "$BUILD_DIR/usr/share/man/man1/prazycron.1.gz"

cat > "$BUILD_DIR/DEBIAN/control" <<EOF
Package: prazycron
Version: $VERSION
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-tk, cron, util-linux, systemd, pkexec | policykit-1, xdg-utils
Recommends: fonts-noto-core
Maintainer: Prazynka <Prazynka@users.noreply.github.com>
Description: Graphical and terminal Cron task manager
 PrazyCron Task Manager provides a dark-by-default graphical interface and a
 Midnight Commander-inspired terminal interface for Cron jobs and systemd timers.
 It validates changes, previews diffs, calculates future runs, runs jobs on demand,
 stores execution history, diagnoses common faults, detects conflicts, manages
 environment variables, backups, tags and templates. The analyzer works offline.
EOF

cat > "$BUILD_DIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 0755 "$BUILD_DIR/DEBIAN/postinst"

cat > "$BUILD_DIR/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 0755 "$BUILD_DIR/DEBIAN/postrm"

find "$BUILD_DIR" -type d -exec chmod 0755 {} +
find "$BUILD_DIR" -type d -exec chmod g-s {} +
find "$BUILD_DIR" -type f ! -path '*/DEBIAN/postinst' ! -path '*/DEBIAN/postrm' ! -path '*/usr/bin/prazycron' ! -path '*/usr/bin/prazycron-run' -exec chmod 0644 {} +
dpkg-deb --root-owner-group --build "$BUILD_DIR" "$PKG"
echo "$PKG"
