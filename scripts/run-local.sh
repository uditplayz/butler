#!/usr/bin/env bash
# Runs MediaMTX + srtla_rec + butler natively (macOS or Linux), restarting any that die.
# Ctrl-C stops everything. On a Mac, caffeinate keeps it awake while running.
set -uo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || ./scripts/init.sh
[ -f runtime/mediamtx.yml ] || ./scripts/init.sh
[ -f "$PWD/runtime/mediamtx.yml" ] || { echo "runtime/mediamtx.yml missing; run ./scripts/init.sh" >&2; exit 1; }
[ -x runtime/bin/srtla_rec ] || [ "$(uname)" = Darwin ] || ./scripts/build-srtla.sh

# Load .env without `source` so values containing spaces are fine.
while IFS='=' read -r key value; do
  case "$key" in ''|\#*) continue ;; esac
  export "$key=${value%$'\r'}"
done < .env

command -v ffmpeg >/dev/null || { echo "ffmpeg not found (brew install ffmpeg)" >&2; exit 1; }
MEDIAMTX_BIN=$(command -v mediamtx || true)
[ -n "$MEDIAMTX_BIN" ] || { echo "mediamtx not found (brew install mediamtx)" >&2; exit 1; }

if [ "$(uname)" = Darwin ]; then
  caffeinate -dimsu -w $$ &   # no sleep while this script runs
fi

supervise() {  # supervise NAME CMD...  - restart forever with a short delay
  local name=$1; shift
  while true; do
    "$@"
    echo "[run-local] $name exited ($?), restarting in 2s" >&2
    sleep 2
  done
}

pids=()
cleanup() { trap - INT TERM EXIT; kill "${pids[@]}" 2>/dev/null; pkill -P $$ 2>/dev/null; wait 2>/dev/null; }
trap cleanup INT TERM EXIT

supervise mediamtx "$MEDIAMTX_BIN" "$PWD/runtime/mediamtx.yml" & pids+=($!)
if [ -x runtime/bin/srtla_rec ]; then   # Linux only; macOS has no srtla_rec (uses epoll)
  supervise srtla "$PWD/runtime/bin/srtla_rec" --srtla_port "${SRTLA_PORT:-5000}" --srt_hostname 127.0.0.1 --srt_port 8890 & pids+=($!)
fi
sleep 1
supervise butler python3 -m butler & pids+=($!)

echo "[run-local] up. Status: http://127.0.0.1:${STATUS_PORT:-8080}/   (Ctrl-C to stop)"
wait
