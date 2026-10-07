"""Feeds raw video+audio through pipes into ffmpeg in several configurations and reports which stall."""
import os, subprocess, sys, threading, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from butler.config import load  # noqa: E402

cfg = load({})
W, H, FPS = cfg.width, cfg.height, cfg.fps
VIDEO = bytes(cfg.frame_bytes)
AUDIO = bytes(cfg.audio_bytes_per_tick)
SECONDS = 6


PROBE = ["-probesize", "32", "-analyzeduration", "0"]


def vin(fd, probe=True):
    return (PROBE if probe else []) + ["-f", "rawvideo", "-pix_fmt", "yuv420p", "-video_size", f"{W}x{H}", "-framerate", str(FPS), "-i", f"pipe:{fd}"]


def ain(fd, probe=True):
    return (PROBE if probe else []) + ["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", f"pipe:{fd}"]


NULL = ["-f", "null", "-"]
X264 = ["-c:v", "libx264", "-preset", "veryfast"]
VARIANTS = {
    "butler's real settings":    (True, True,  None),
    "copy, no encoding":         (True, True,  lambda v, a: vin(v) + ain(a) + ["-c", "copy"] + NULL),
    "x264+aac, simple":          (True, True,  lambda v, a: vin(v) + ain(a) + X264 + ["-c:a", "aac"] + NULL),
    "x264+aac, -threads 2":      (True, True,  lambda v, a: vin(v) + ain(a) + X264 + ["-threads", "2", "-c:a", "aac"] + NULL),
    "x264+aac, no probe limits": (True, True,  lambda v, a: vin(v, False) + ain(a, False) + X264 + ["-c:a", "aac"] + NULL),
    "video only":                (True, False, lambda v, a: vin(v) + X264 + NULL),
    "audio only":                (False, True, lambda v, a: ain(a) + ["-c:a", "aac"] + NULL),
}


def real_cmd(v, a):
    from butler.compositor import Compositor
    comp = Compositor(cfg, VIDEO, None, threading.Event())
    return comp.encoder_command(v, a)[1:-3] + NULL


def run_real(label):
    from butler.compositor import Compositor
    comp = Compositor(cfg, VIDEO, None, threading.Event())
    orig = comp.encoder_command
    comp.encoder_command = lambda v, a: orig(v, a)[:-3] + NULL
    t = threading.Thread(target=comp._run_encoder, daemon=True)
    t.start()
    time.sleep(SECONDS)
    n = comp.stats["ticks"]
    comp.stop.set()
    t.join(5)
    verdict = "OK  " if n >= FPS * (SECONDS - 1.5) else "STALL"
    print(f"  {verdict} {label:<27} {n:>3} ticks in {SECONDS}s")
    sys.stdout.flush()


def run(label, feed_v, feed_a, build):
    vr, vw = os.pipe()
    ar, aw = os.pipe()
    cmd = [cfg.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"] + (build or real_cmd)(vr, ar)
    proc = subprocess.Popen(cmd, pass_fds=(vr, ar), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    os.close(vr), os.close(ar)
    ticks = [0]

    def _all(fd, data):
        mv = memoryview(data)
        while mv:
            mv = mv[os.write(fd, mv):]

    def feeder(fd, data, count):
        nxt = time.monotonic()
        try:
            while True:
                _all(fd, data)
                if count:
                    ticks[0] += 1
                nxt += 1 / FPS
                time.sleep(max(0, nxt - time.monotonic()))
        except OSError:
            pass

    # One thread per pipe, like butler: a single sequential writer can deadlock newer ffmpegs.
    if feed_v:
        threading.Thread(target=feeder, args=(vw, VIDEO, True), daemon=True).start()
    if feed_a:
        threading.Thread(target=feeder, args=(aw, AUDIO, not feed_v), daemon=True).start()
    time.sleep(SECONDS)
    n = ticks[0]
    proc.kill()
    for fd in (vw, aw):
        try:
            os.close(fd)
        except OSError:
            pass
    err = proc.stderr.read().decode("utf-8", "replace").strip().splitlines()
    verdict = "OK  " if n >= FPS * (SECONDS - 1.5) else "STALL"
    print(f"  {verdict} {label:<27} {n:>3} ticks in {SECONDS}s" + (f"   [{err[-1][:70]}]" if err and verdict != "OK  " else ""))
    sys.stdout.flush()


print(f"ffmpeg: {cfg.ffmpeg}")
for label, (fv, fa, build) in VARIANTS.items():
    if build is None:
        run_real(label)
    else:
        run(label, fv, fa, build)
