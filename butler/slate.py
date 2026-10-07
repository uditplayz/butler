"""Renders the "be right back" frame shown while the phone is disconnected."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

from .config import Config

log = logging.getLogger("slate")
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


def _run(cmd: list[str], expected: int) -> bytes | None:
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    if result.returncode != 0 or len(result.stdout) != expected:
        log.debug("slate ffmpeg failed: %s", result.stderr.decode("utf-8", "replace")[-400:])
        return None
    return result.stdout


def render(cfg: Config) -> bytes:
    """Return one raw yuv420p frame of the configured size."""
    w, h, size = cfg.width, cfg.height, cfg.frame_bytes
    base = [cfg.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    out = ["-frames:v", "1", "-pix_fmt", "yuv420p", "-f", "rawvideo", "pipe:1"]
    fit = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
           f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")

    if cfg.slate_image and os.path.isfile(cfg.slate_image):
        frame = _run(base + ["-i", cfg.slate_image, "-vf", fit] + out, size)
        if frame:
            return frame
        log.warning("could not use SLATE_IMAGE %s; falling back to the text slate", cfg.slate_image)

    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for key, text in (("title", cfg.slate_text), ("sub", cfg.slate_subtext)):
            files[key] = os.path.join(tmp, key + ".txt")
            with open(files[key], "w", encoding="utf-8") as fh:
                fh.write(text)
        font = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
        font_opt = f":fontfile={font}" if font else ""
        vf = (
            f"drawtext=textfile={files['title']}{font_opt}:fontcolor=white:fontsize=h/10:"
            f"x=(w-text_w)/2:y=(h-text_h)/2-h/14,"
            f"drawtext=textfile={files['sub']}{font_opt}:fontcolor=0xadadb8:fontsize=h/26:"
            f"x=(w-text_w)/2:y=(h/2)+h/16"
        )
        src = ["-f", "lavfi", "-i", f"color=c=0x18181b:s={w}x{h}:d=1"]
        frame = _run(base + src + ["-vf", vf] + out, size)
        if frame:
            return frame

    log.warning("drawtext unavailable; using a plain dark slate")
    # Y=24 U=V=128 is a near-black frame in yuv420p.
    luma = w * h
    return bytes([24]) * luma + bytes([128]) * (size - luma)
