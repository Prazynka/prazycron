#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VERSION="$(python3 -c 'from prazycron import __version__; print(__version__)')"
./scripts/check-release.sh
rm -rf dist build
mkdir -p dist
./build-deb.sh "$ROOT/build/deb-root" "$ROOT/dist"
SOURCE="dist/prazycron-${VERSION}-source.tar.gz"
tar --transform="s,^\./,prazycron-${VERSION}/," --exclude='./.git' --exclude='./build' --exclude='./dist' --exclude='*/__pycache__' -czf "$SOURCE" .
(
  cd dist
  sha256sum "prazycron_${VERSION}_all.deb" "prazycron-${VERSION}-source.tar.gz" > SHA256SUMS
)
echo "Release assets are in $ROOT/dist"
