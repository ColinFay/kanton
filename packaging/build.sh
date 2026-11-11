#!/bin/bash
# Build dist/kanton.app (+ a DMG) on macOS. Needs: the dev venv (.venv) with pyinstaller + pywebview, Homebrew ffmpeg.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
rm -rf build/ffmpeg-bin dist/kanton dist/kanton.app
$PY packaging/bundle_ffmpeg.py build/ffmpeg-bin
$PY -m PyInstaller --noconfirm --clean --log-level WARN packaging/kanton.spec
/usr/bin/codesign --force --deep --sign - dist/kanton.app
VERSION=$(grep '^version' pyproject.toml | cut -d'"' -f2)
rm -f "dist/kanton-$VERSION-macos-arm64.dmg"
/usr/bin/hdiutil create -quiet -volname kanton -srcfolder dist/kanton.app -ov -format UDZO "dist/kanton-$VERSION-macos-arm64.dmg"
du -sh dist/kanton.app "dist/kanton-$VERSION-macos-arm64.dmg"
echo "unsigned build: on another Mac, run  xattr -dr com.apple.quarantine kanton.app  (or right-click ▸ Open) the first time"
