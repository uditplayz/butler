#!/usr/bin/env bash
# Generates .env (with random ingest credentials) on first run and renders runtime/mediamtx.yml.
# Safe to re-run: existing .env values are kept.
set -euo pipefail
cd "$(dirname "$0")/.."

rand() { openssl rand -hex "$(( $1 / 2 ))"; }   # $1 hex chars; portable on macOS and Linux

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env - now edit it and add your TWITCH_STREAM_KEY / YOUTUBE_STREAM_KEY."
fi

get() { grep -E "^$1=" .env | head -n1 | cut -d= -f2- | tr -d '\r'; }
set_env() {  # set_env KEY VALUE - replace the line, or append it if missing
  if grep -qE "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; else echo "$1=$2" >> .env; fi
}
# Fill in anything empty (also covers a hand-copied .env). Existing values are never overwritten.
[ -n "$(get PUBLISH_USER)" ] || set_env PUBLISH_USER phone
[ -n "$(get PUBLISH_PASS)" ] || set_env PUBLISH_PASS "$(rand 24)"
for kv in MEDIAMTX_API_PORT=9997 MEDIAMTX_RTSP_PORT=8554; do
  [ -n "$(get "${kv%%=*}")" ] || set_env "${kv%%=*}" "${kv#*=}"
done

PUBLISH_USER=$(get PUBLISH_USER); PUBLISH_PASS=$(get PUBLISH_PASS)
API_PORT=$(get MEDIAMTX_API_PORT); RTSP_PORT=$(get MEDIAMTX_RTSP_PORT)
for v in PUBLISH_USER PUBLISH_PASS; do
  [ -n "${!v}" ] || { echo "$v is empty in .env" >&2; exit 1; }
  [[ "${!v}" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "$v may only contain letters, digits, _ . -" >&2; exit 1; }
done

# On macOS the default dual-stack ":8890" socket ends up IPv6-only and ignores IPv4 clients, so bind
# IPv4 explicitly there. Set SRT_BIND in .env (e.g. "[::]:8890") to override.
SRT_ADDRESS=$(get SRT_BIND)
if [ -z "$SRT_ADDRESS" ]; then
  if [ "$(uname)" = Darwin ]; then SRT_ADDRESS=0.0.0.0:8890; else SRT_ADDRESS=:8890; fi
fi

mkdir -p runtime
umask 077
sed -e "s|__PUBLISH_USER__|$PUBLISH_USER|" -e "s|__PUBLISH_PASS__|$PUBLISH_PASS|" \
    -e "s|__SRT_ADDRESS__|$SRT_ADDRESS|" -e "s|__API_PORT__|${API_PORT:-9997}|" -e "s|__RTSP_PORT__|${RTSP_PORT:-8554}|" \
    mediamtx/mediamtx.yml.tmpl > runtime/mediamtx.yml
chmod 644 runtime/mediamtx.yml  # container user needs to read it; the directory is not exposed

echo "Rendered runtime/mediamtx.yml"
echo
echo "Moblin / OBS publish settings:"
echo "  SRT stream ID : publish:live:$PUBLISH_USER:$PUBLISH_PASS"
