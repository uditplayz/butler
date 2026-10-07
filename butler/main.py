"""Entry point: wires config, compositor, relays and the status server together."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

from . import __version__, slate, status
from .compositor import Compositor
from .config import Config, ConfigError, load
from .relay import Relay
from .util import MediaMTX, Redactor


def setup_logging(cfg: Config) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(Redactor(cfg.secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(cfg.log_level)


def main() -> int:
    try:
        cfg = load(os.environ)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg)
    log = logging.getLogger("butler")
    log.info("butler %s starting in %s mode (%dx%d@%d, %d kbps)", __version__, cfg.mode,
             cfg.width, cfg.height, cfg.fps, cfg.video_kbps)
    if not cfg.destinations:
        log.warning("no destinations configured; set TWITCH_STREAM_KEY and/or YOUTUBE_STREAM_KEY")

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    mtx = MediaMTX(cfg.mtx_host, cfg.mtx_api_port)
    compositor = None
    if cfg.mode == "resilient":
        compositor = Compositor(cfg, slate.render(cfg), mtx, stop)
        compositor.start()
    relays = [Relay(cfg, d, mtx, stop) for d in cfg.destinations]
    for relay in relays:
        relay.start()

    started = time.monotonic()

    def snapshot() -> dict:
        paths = mtx.paths() or {}
        return {
            "version": __version__,
            "mode": cfg.mode,
            "uptime_seconds": round(time.monotonic() - started),
            "mediamtx_reachable": mtx.paths() is not None,
            "ingest_live": bool(paths.get(cfg.live_path, {}).get("ready")),
            "compositor": compositor.snapshot() if compositor else None,
            "destinations": {r.dest.name: r.snapshot() for r in relays},
        }

    def healthy() -> bool:
        # Healthy = the supervisors are running. A disconnected phone is normal, not unhealthy.
        return all(r.is_alive() for r in relays) and (compositor is None or compositor.alive())

    status.serve(cfg.status_bind, cfg.status_port, snapshot, healthy)
    stop.wait()
    log.info("shutting down")
    time.sleep(1.0)  # let supervisors terminate their ffmpeg children
    return 0


if __name__ == "__main__":
    sys.exit(main())
