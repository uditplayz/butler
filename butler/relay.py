"""One supervised ffmpeg process per destination (Twitch, YouTube, ...)."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from .config import Config, Destination
from .util import Backoff, MediaMTX, pump_lines, terminate

CONNECT_GRACE_S = 30.0  # time allowed to connect before the first progress report
STALL_S = 20.0  # no progress for this long while connected => kill and reconnect


class Relay(threading.Thread):
    def __init__(self, cfg: Config, dest: Destination, mtx: MediaMTX, stop: threading.Event):
        super().__init__(name=f"relay-{dest.name}", daemon=True)
        self.cfg, self.dest, self.mtx, self.stop = cfg, dest, mtx, stop
        self.log = logging.getLogger(f"relay.{dest.name}")
        self.state = "waiting"
        self.restarts = 0
        self.bitrate = ""
        self.connected_since: float | None = None
        self._last_progress = 0.0

    def command(self) -> list[str]:
        return [
            self.cfg.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-progress", "pipe:1", "-nostats", "-stats_period", "1",
            "-rtsp_transport", "tcp", "-timeout", "10000000",
            "-i", self.cfg.rtsp_url(self.cfg.relay_source_path),
            "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-flvflags", "no_duration_filesize", "-f", "flv", self.dest.url,
        ]

    def _watch_progress(self, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("bitrate="):
                self.bitrate = line.split("=", 1)[1]
            elif line.startswith("progress="):
                self._last_progress = time.monotonic()
                if self.connected_since is None:
                    self.connected_since = self._last_progress
                    self.state = "live"
                    self.log.info("streaming to %s", self.dest.display)

    def _run_once(self) -> float:
        started = time.monotonic()
        self._last_progress = started
        self.connected_since = None
        self.state = "connecting"
        proc = subprocess.Popen(
            self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        pump_lines(proc.stderr, self.log)
        threading.Thread(target=self._watch_progress, args=(proc,), daemon=True).start()
        try:
            while proc.poll() is None and not self.stop.is_set():
                idle = time.monotonic() - self._last_progress
                limit = STALL_S if self.connected_since else CONNECT_GRACE_S
                if idle > limit:
                    self.log.warning("no progress for %.0fs, reconnecting", idle)
                    break
                self.stop.wait(0.5)
        finally:
            terminate(proc)
        self.bitrate = ""
        return time.monotonic() - started

    def run(self) -> None:
        backoff = Backoff(2.0, 20.0, healthy_after=30.0)
        while not self.stop.is_set():
            ready = self.mtx.is_ready(self.cfg.relay_source_path)
            if ready is False:
                self.state = "waiting"
                self.stop.wait(1.0)
                continue
            ran = self._run_once()
            if self.stop.is_set():
                break
            self.restarts += 1
            self.state = "retrying"
            delay = backoff.next(ran)
            self.log.warning("relay to %s ended after %.0fs; retrying in %.1fs",
                             self.dest.display, ran, delay)
            self.stop.wait(delay)
        self.state = "stopped"

    def snapshot(self) -> dict:
        since = self.connected_since
        return {
            "destination": self.dest.display,
            "state": self.state,
            "bitrate": self.bitrate,
            "restarts": self.restarts,
            "connected_seconds": round(time.monotonic() - since) if since and self.state == "live" else 0,
        }
