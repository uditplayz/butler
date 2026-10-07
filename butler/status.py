"""Read-only status endpoint: /healthz, /status (JSON) and a tiny HTML page at /."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Butler</title><style>body{font:16px system-ui;background:#0e0e10;color:#efeff1;max-width:42rem;margin:2rem auto;padding:0 1rem}
pre{background:#18181b;padding:1rem;border-radius:8px;overflow:auto}</style>
<h1>Butler</h1><pre id=o>loading…</pre>
<script>async function t(){try{o.textContent=JSON.stringify(await (await fetch('status')).json(),null,2)}catch(e){o.textContent=e}}
t();setInterval(t,2000)</script>"""


def serve(bind: str, port: int, snapshot: Callable[[], dict], healthy: Callable[[], bool]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                ok = healthy()
                self._send(200 if ok else 503, b"ok\n" if ok else b"unhealthy\n", "text/plain")
            elif self.path == "/status":
                self._send(200, json.dumps(snapshot(), indent=2).encode(), "application/json")
            elif self.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found\n", "text/plain")

        def log_message(self, *args) -> None:  # silence per-request logging
            pass

    server = ThreadingHTTPServer((bind, port), Handler)
    threading.Thread(target=server.serve_forever, name="status", daemon=True).start()
    logging.getLogger("status").info("status page on http://%s:%d/", bind, port)
    return server
