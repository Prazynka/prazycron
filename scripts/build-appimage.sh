#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VERSION="$(python3 - <<'PY'
from pathlib import Path
import re
text = Path('pyproject.toml').read_text(encoding='utf-8')
match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
if not match:
    raise SystemExit('Unable to determine version from pyproject.toml')
print(match.group(1))
PY
)"
PYTHON_BIN="$(readlink -f "$(command -v python3)")"
PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_STDLIB="$(python3 -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')"
ARCH="${ARCH:-x86_64}"
APPDIR="$ROOT_DIR/build/PrazyCron.AppDir"
TOOLS_DIR="$ROOT_DIR/build/appimage-tools"
OUT_DIR="$ROOT_DIR/dist"
OUT_FILE="$OUT_DIR/PrazyCron-${VERSION}-${ARCH}.AppImage"
ZSYNC_FILE="$OUT_FILE.zsync"
ROOT_ZSYNC_FILE="$ROOT_DIR/$(basename "$OUT_FILE").zsync"

rm -rf "$APPDIR"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/lib" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/metainfo" \
  "$APPDIR/usr/share/icons/hicolor/512x512/apps" \
  "$APPDIR/usr/share/prazycron" \
  "$TOOLS_DIR" \
  "$OUT_DIR"

install -Dm755 "$PYTHON_BIN" "$APPDIR/usr/bin/python3"
cp -a "$PYTHON_STDLIB" "$APPDIR/usr/lib/"
cp -a prazycron "$APPDIR/usr/lib/prazycron"

for tcl_dir in /usr/share/tcltk /usr/lib/tcltk; do
  if [[ -d "$tcl_dir" ]]; then
    mkdir -p "$APPDIR$(dirname "$tcl_dir")"
    cp -a "$tcl_dir" "$APPDIR$(dirname "$tcl_dir")/"
  fi
done

install -Dm644 assets/prazycron-512.png \
  "$APPDIR/usr/share/icons/hicolor/512x512/apps/prazycron.png"
install -Dm644 assets/status-on.png "$APPDIR/usr/share/prazycron/status-on.png"
install -Dm644 assets/status-off.png "$APPDIR/usr/share/prazycron/status-off.png"
install -Dm644 packaging/net.prazynka.PrazyCron.metainfo.xml \
  "$APPDIR/usr/share/metainfo/net.prazynka.PrazyCron.metainfo.xml"

python3 - "$VERSION" <<'PY'
from pathlib import Path
import sys
version = sys.argv[1]
src = Path('packaging/prazycron.desktop').read_text(encoding='utf-8')
lines = []
for line in src.splitlines():
    if line.startswith('Exec='):
        line = 'Exec=prazycron --gui'
    lines.append(line)
lines.append(f'X-AppImage-Version={version}')
Path('build/PrazyCron.AppDir/usr/share/applications/prazycron.desktop').write_text(
    '\n'.join(lines) + '\n', encoding='utf-8'
)
PY

cat > "$APPDIR/AppRun" <<EOF_RUN
#!/usr/bin/env bash
set -e
HERE="\$(cd "\$(dirname "\$(readlink -f "\$0")")" && pwd)"
export PYTHONHOME="\$HERE/usr"
export PYTHONPATH="\$HERE/usr/lib\${PYTHONPATH:+:\$PYTHONPATH}"
export PATH="\$HERE/usr/bin:\$PATH"
export LD_LIBRARY_PATH="\$HERE/usr/lib:\$HERE/usr/lib/python${PYTHON_VERSION}/lib-dynload\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
if [[ -d "\$HERE/usr/share/tcltk/tcl8.6" ]]; then
  export TCL_LIBRARY="\$HERE/usr/share/tcltk/tcl8.6"
fi
if [[ -d "\$HERE/usr/share/tcltk/tk8.6" ]]; then
  export TK_LIBRARY="\$HERE/usr/share/tcltk/tk8.6"
fi
exec "\$HERE/usr/bin/python3" -m prazycron.main "\$@"
EOF_RUN
chmod +x "$APPDIR/AppRun"

ln -sfn usr/share/applications/prazycron.desktop "$APPDIR/prazycron.desktop"
ln -sfn usr/share/icons/hicolor/512x512/apps/prazycron.png "$APPDIR/prazycron.png"
ln -sfn prazycron.png "$APPDIR/.DirIcon"

LINUXDEPLOY="$TOOLS_DIR/linuxdeploy-${ARCH}.AppImage"
APPIMAGETOOL="$TOOLS_DIR/appimagetool-${ARCH}.AppImage"

if [[ ! -x "$LINUXDEPLOY" ]]; then
  curl --fail --location --retry 3 \
    "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-${ARCH}.AppImage" \
    --output "$LINUXDEPLOY"
  chmod +x "$LINUXDEPLOY"
fi
if [[ ! -x "$APPIMAGETOOL" ]]; then
  curl --fail --location --retry 3 \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage" \
    --output "$APPIMAGETOOL"
  chmod +x "$APPIMAGETOOL"
fi

export APPIMAGE_EXTRACT_AND_RUN=1

deploy_args=(
  --appdir "$APPDIR"
  --executable "$APPDIR/usr/bin/python3"
  --desktop-file "$APPDIR/usr/share/applications/prazycron.desktop"
  --icon-file "$APPDIR/usr/share/icons/hicolor/512x512/apps/prazycron.png"
)

while IFS= read -r -d '' module; do
  deploy_args+=(--library "$module")
done < <(find "$APPDIR/usr/lib/python${PYTHON_VERSION}/lib-dynload" -type f -name '*.so' -print0)

"$LINUXDEPLOY" "${deploy_args[@]}"

rm -f "$OUT_FILE" "$ZSYNC_FILE" "$ROOT_ZSYNC_FILE"
UPDATE_INFO="gh-releases-zsync|Prazynka|prazycron|latest|PrazyCron-*-${ARCH}.AppImage.zsync"
ARCH="$ARCH" "$APPIMAGETOOL" -u "$UPDATE_INFO" "$APPDIR" "$OUT_FILE"
chmod +x "$OUT_FILE"

if [[ -f "$ROOT_ZSYNC_FILE" && "$ROOT_ZSYNC_FILE" != "$ZSYNC_FILE" ]]; then
  mv "$ROOT_ZSYNC_FILE" "$ZSYNC_FILE"
fi

sha256sum "$OUT_FILE" > "$OUT_FILE.sha256"
if [[ -f "$ZSYNC_FILE" ]]; then
  sha256sum "$ZSYNC_FILE" > "$ZSYNC_FILE.sha256"
fi

printf 'Built: %s\n' "$OUT_FILE"
