"""Small shared helpers: secret redaction, backoff, process plumbing, MediaMTX API."""

from __future__ import annotations

import json
import logging
import random
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import IO, Iterable


class Redactor(logging.Filter):
    """Scrubs stream keys from every log record (ffmpeg likes to echo URLs)."""

    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


class Backoff:
    """Exponential backoff that resets once a run has been healthy for a while."""

    def __init__(self, minimum: float = 1.0, maximum: float = 15.0, healthy_after: float = 30.0):
        self.minimum, self.maximum, self.healthy_after = minimum, maximum, healthy_after
        self._current = minimum

    def next(self, ran_for: float) -> float:
        if ran_for >= self.healthy_after:
            self._current = self.minimum
        delay = self._current
        self._current = min(self.maximum, self._current * 2)
        return delay * random.uniform(0.9, 1.1)


def pump_lines(stream: IO[bytes], logger: logging.Logger, level: int = logging.WARNING) -> threading.Thread:
    """Forward a subprocess stream to the logger, line by line, on a daemon thread."""

    def run() -> None:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                logger.log(level, "%s", line)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def terminate(proc: subprocess.Popen, grace: float = 3.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class MediaMTX:
    """Tiny client for the MediaMTX control API (localhost only)."""

    def __init__(self, host: str, api_port: int):
        self._base = f"http://{host}:{api_port}/v3"
        self._cache: tuple[float, dict[str, dict]] = (0.0, {})
        self._lock = threading.Lock()

    def paths(self, max_age: float = 1.0) -> dict[str, dict] | None:
        """Map of path name -> info, or None if the API is unreachable."""
        with self._lock:
            ts, cached = self._cache
            if time.monotonic() - ts < max_age:
                return cached
        try:
            with urllib.request.urlopen(f"{self._base}/paths/list?itemsPerPage=100", timeout=2) as r:
                data = json.load(r)
        except (urllib.error.URLError, OSError, ValueError):
            return None
        result = {item["name"]: item for item in data.get("items", [])}
        with self._lock:
            self._cache = (time.monotonic(), result)
        return result

    def is_ready(self, path: str) -> bool | None:
        """True/False if known, None if MediaMTX can't be queried."""
        paths = self.paths()
        if paths is None:
            return None
        return bool(paths.get(path, {}).get("ready"))
