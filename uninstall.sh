#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run with sudo: sudo ./uninstall.sh" >&2
  exit 1
fi
rm -rf /usr/lib/python3/dist-packages/prazycron
rm -f /usr/bin/prazycron
rm -f /usr/bin/prazycron-run
rm -rf /usr/share/prazycron
rm -f /usr/share/applications/prazycron.desktop
rm -f /usr/share/metainfo/net.prazynka.PrazyCron.metainfo.xml
rm -f /usr/share/man/man1/prazycron.1.gz
rm -rf /usr/share/doc/prazycron
for size in 16 24 32 48 64 128 256 512; do
  rm -f "/usr/share/icons/hicolor/${size}x${size}/apps/prazycron.png"
done
command -v update-desktop-database >/dev/null && update-desktop-database /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true
echo "PrazyCron removed. User settings and backups were left untouched."
