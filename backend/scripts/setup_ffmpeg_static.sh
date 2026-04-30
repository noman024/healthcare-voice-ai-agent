#!/usr/bin/env bash
# Download a static ffmpeg build into third_party/ffmpeg-static/ (no apt/sudo).
# After this, set MUSETALK_FFMPEG_PATH=third_party/ffmpeg-static/current in backend/.env (repo-root relative).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/third_party/ffmpeg-static"
mkdir -p "$DEST"
cd "$DEST"
if [[ -x "$DEST/current/ffmpeg" ]]; then
  echo "ffmpeg already present at $DEST/current/ffmpeg"
  "$DEST/current/ffmpeg" -version | head -1
  exit 0
fi
ARCHIVE="${ARCHIVE:-/tmp/ffmpeg-release-amd64-static.tar.xz}"
if [[ ! -f "$ARCHIVE" ]]; then
  curl -fsSL -o "$ARCHIVE" https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
fi
rm -rf ffmpeg-* 2>/dev/null || true
tar -xf "$ARCHIVE"
DIR="$(find "$DEST" -maxdepth 1 -type d -name 'ffmpeg-*-amd64-static' | head -1)"
if [[ -z "$DIR" || ! -x "$DIR/ffmpeg" ]]; then
  echo "extract failed under $DEST" >&2
  exit 1
fi
ln -sfn "$(basename "$DIR")" "$DEST/current"
echo "OK: $DEST/current/ffmpeg"
"$DEST/current/ffmpeg" -version | head -1
