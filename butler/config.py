"""Configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlsplit

MODES = ("resilient", "passthrough")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Destination:
    name: str
    url: str  # full URL including stream key; treat as secret
    secrets: tuple[str, ...] = ()

    @property
    def display(self) -> str:
        parts = urlsplit(self.url)
        return f"{parts.scheme}://{parts.hostname}"


@dataclass(frozen=True)
class Config:
    mode: str = "resilient"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_kbps: int = 6000
    audio_kbps: int = 160
    x264_preset: str = "veryfast"
    jitter_ms: int = 300
    live_stall_s: float = 3.0
    slate_hold_s: float = 2.0
    slate_text: str = "Be right back"
    slate_subtext: str = "The stream will resume automatically"
    slate_image: str = ""
    destinations: tuple[Destination, ...] = ()
    mtx_host: str = "127.0.0.1"
    mtx_rtsp_port: int = 8554
    mtx_rtmp_port: int = 1935
    mtx_api_port: int = 9997
    live_path: str = "live"
    program_path: str = "program"
    status_bind: str = "127.0.0.1"
    status_port: int = 8080
    ffmpeg: str = "ffmpeg"
    log_level: str = "INFO"
    secrets: tuple[str, ...] = field(default=(), repr=False)

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 3 // 2

    @property
    def samples_per_tick(self) -> int:
        return 48000 // self.fps

    @property
    def audio_bytes_per_tick(self) -> int:
        return self.samples_per_tick * 4  # s16le stereo

    @property
    def jitter_frames(self) -> int:
        return max(2, math.ceil(self.jitter_ms / 1000 * self.fps))

    def rtsp_url(self, path: str) -> str:
        return f"rtsp://{self.mtx_host}:{self.mtx_rtsp_port}/{path}"

    @property
    def relay_source_path(self) -> str:
        return self.program_path if self.mode == "resilient" else self.live_path


def _int(env: Mapping[str, str], key: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}, got {value}")
    return value


def _float(env: Mapping[str, str], key: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}, got {value}")
    return value


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    return env.get(key, "").strip() or default


_KEY_RE = re.compile(r"^[A-Za-z0-9_\-?=&.]+$")


def _destinations(env: Mapping[str, str]) -> tuple[list[Destination], list[str]]:
    dests: list[Destination] = []
    secrets: list[str] = []

    def add(name: str, base: str, key: str) -> None:
        if not key:
            return
        if not _KEY_RE.match(key):
            raise ConfigError(f"{name.upper()}_STREAM_KEY contains unexpected characters")
        dests.append(Destination(name, f"{base.rstrip('/')}/{key}", (key,)))
        secrets.append(key)

    add("twitch", _str(env, "TWITCH_INGEST_URL", "rtmp://live.twitch.tv/app"),
        env.get("TWITCH_STREAM_KEY", "").strip())
    add("youtube", _str(env, "YOUTUBE_INGEST_URL", "rtmp://a.rtmp.youtube.com/live2"),
        env.get("YOUTUBE_STREAM_KEY", "").strip())

    for i, url in enumerate(u.strip() for u in env.get("CUSTOM_RTMP_URLS", "").split(",")):
        if not url:
            continue
        if urlsplit(url).scheme not in ("rtmp", "rtmps"):
            raise ConfigError("CUSTOM_RTMP_URLS entries must start with rtmp:// or rtmps://")
        parts = urlsplit(url)
        dests.append(Destination(f"custom{i + 1}", url, (url, parts.path.lstrip("/"))))
        secrets.extend((url, parts.path.lstrip("/")))
    return dests, [s for s in secrets if len(s) >= 4]


def load(env: Mapping[str, str]) -> Config:
    mode = _str(env, "MODE", "resilient").lower()
    if mode not in MODES:
        raise ConfigError(f"MODE must be one of {MODES}, got {mode!r}")

    size = _str(env, "VIDEO_SIZE", "1920x1080")
    m = re.fullmatch(r"(\d{3,4})x(\d{3,4})", size)
    if not m:
        raise ConfigError(f"VIDEO_SIZE must look like 1920x1080, got {size!r}")
    width, height = int(m.group(1)), int(m.group(2))
    if width % 2 or height % 2:
        raise ConfigError("VIDEO_SIZE dimensions must be even")

    fps = _int(env, "VIDEO_FPS", 30, 15, 60)
    if 48000 % fps:
        raise ConfigError("VIDEO_FPS must divide 48000 evenly (use 24, 25, 30, 48, 50 or 60)")

    dests, secrets = _destinations(env)
    bind = _str(env, "STATUS_BIND", "127.0.0.1")
    return Config(
        mode=mode,
        width=width,
        height=height,
        fps=fps,
        video_kbps=_int(env, "VIDEO_BITRATE_KBPS", 6000, 500, 20000),
        audio_kbps=_int(env, "AUDIO_BITRATE_KBPS", 160, 64, 320),
        x264_preset=_str(env, "X264_PRESET", "veryfast"),
        jitter_ms=_int(env, "JITTER_MS", 300, 100, 3000),
        live_stall_s=_float(env, "LIVE_STALL_SECONDS", 3.0, 0.5, 60),
        slate_hold_s=_float(env, "SLATE_MIN_HOLD_SECONDS", 2.0, 0, 60),
        slate_text=_str(env, "SLATE_TEXT", "Be right back"),
        slate_subtext=_str(env, "SLATE_SUBTEXT", "The stream will resume automatically"),
        slate_image=env.get("SLATE_IMAGE", "").strip(),
        destinations=tuple(dests),
        mtx_host=_str(env, "MEDIAMTX_HOST", "127.0.0.1"),
        mtx_rtsp_port=_int(env, "MEDIAMTX_RTSP_PORT", 8554, 1, 65535),
        mtx_rtmp_port=_int(env, "MEDIAMTX_RTMP_PORT", 1935, 1, 65535),
        mtx_api_port=_int(env, "MEDIAMTX_API_PORT", 9997, 1, 65535),
        status_bind=bind,
        status_port=_int(env, "STATUS_PORT", 8080, 1, 65535),
        ffmpeg=_str(env, "FFMPEG_BIN", "ffmpeg"),
        log_level=_str(env, "LOG_LEVEL", "INFO").upper(),
        secrets=tuple(secrets),
    )
