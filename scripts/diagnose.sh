#!/usr/bin/env bash
# Pinpoints why the encoder stalls. Stop run-local.sh first (Ctrl-C), then run: ./scripts/diagnose.sh
cd "$(dirname "$0")/.."
echo "== versions"; ffmpeg -version | head -1; python3 --version; command -v mediamtx
ffmpeg -hide_banner -encoders 2>/dev/null | grep -E " libx264 " | head -1
ffmpeg -hide_banner -filters 2>/dev/null | grep -c drawtext | sed 's/^/drawtext filters: /'

echo; echo "== A: butler's pipe feed into ffmpeg, output discarded (6s)"
python3 - <<'PY'
import sys, threading, time
sys.path.insert(0, ".")
import butler.compositor as C
from butler.config import load
cfg = load({})
comp = C.Compositor(cfg, bytes(cfg.frame_bytes), None, threading.Event())
orig = comp.encoder_command
comp.encoder_command = lambda v, a: orig(v, a)[:-3] + ["-f", "null", "-"]
t = threading.Thread(target=comp._run_encoder, daemon=True); t.start()
time.sleep(6)
n = comp.stats["ticks"]
print("RESULT A:", "OK" if n > 150 else "FAIL", f"({n} ticks in 6s, expect ~180)")
comp.stop.set(); t.join(5)
PY

[ -f runtime/mediamtx.yml ] || ./scripts/init.sh >/dev/null
mediamtx "$PWD/runtime/mediamtx.yml" >/tmp/butler-diag-mtx.log 2>&1 &
MTX=$!; trap 'kill $MTX 2>/dev/null' EXIT; sleep 2
SRC=(-f lavfi -i "testsrc2=s=1280x720:r=30" -f lavfi -i "sine=f=440:r=48000" -t 6 -c:v libx264 -preset veryfast -c:a aac)

echo; echo "== B: ffmpeg -> MediaMTX over RTMP (6s)"
timeout 25 ffmpeg -hide_banner -loglevel error -re "${SRC[@]}" -f flv rtmp://127.0.0.1:1935/program && echo "RESULT B: OK" || echo "RESULT B: FAIL"
echo; echo "== C: ffmpeg -> MediaMTX over RTSP (6s)"
timeout 25 ffmpeg -hide_banner -loglevel error -re "${SRC[@]}" -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/program && echo "RESULT C: OK" || echo "RESULT C: FAIL"
echo; echo "Send me everything above this line."
