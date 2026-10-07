#!/usr/bin/env bash
# Generates .env (with random ingest credentials) on first run and renders runtime/mediamtx.yml.
# Safe to re-run: existing .env values are kept.
set -euo pipefail
cd "$(dirname "$0")/.."

rand() { head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "$1"; }

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^PUBLISH_USER=.*|PUBLISH_USER=phone|; s|^PUBLISH_PASS=.*|PUBLISH_PASS=$(rand 24)|" .env
  chmod 600 .env
  echo "Created .env - now edit it and add your TWITCH_STREAM_KEY / YOUTUBE_STREAM_KEY."
fi

get() { grep -E "^$1=" .env | head -n1 | cut -d= -f2- | tr -d '\r'; }
PUBLISH_USER=$(get PUBLISH_USER); PUBLISH_PASS=$(get PUBLISH_PASS)
API_PORT=$(get MEDIAMTX_API_PORT); RTSP_PORT=$(get MEDIAMTX_RTSP_PORT)
for v in PUBLISH_USER PUBLISH_PASS; do
  [ -n "${!v}" ] || { echo "$v is empty in .env" >&2; exit 1; }
  [[ "${!v}" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "$v may only contain letters, digits, _ . -" >&2; exit 1; }
done

mkdir -p runtime
umask 077
sed -e "s|__PUBLISH_USER__|$PUBLISH_USER|" -e "s|__PUBLISH_PASS__|$PUBLISH_PASS|" \
    -e "s|__API_PORT__|${API_PORT:-9997}|" -e "s|__RTSP_PORT__|${RTSP_PORT:-8554}|" \
    mediamtx/mediamtx.yml.tmpl > runtime/mediamtx.yml
chmod 644 runtime/mediamtx.yml  # container user needs to read it; the directory is not exposed

echo "Rendered runtime/mediamtx.yml"
echo
echo "Moblin / OBS publish settings:"
echo "  SRT stream ID : publish:live:$PUBLISH_USER:$PUBLISH_PASS"
