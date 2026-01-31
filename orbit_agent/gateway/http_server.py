from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Dict, Any, Tuple


@dataclass
class GatewayStatus:
    started_at: float
    telegram_enabled: bool
    active_tasks: int
    jobs_enabled: int


class GatewayHttpServer:
    """
    Minimal local HTTP server for /health and /status.
    Designed to be dependency-free (stdlib only).
    """

    def __init__(
        self,
        bind: str,
        port: int,
        get_status: Callable[[], GatewayStatus],
        token: Optional[str] = None,
    ):
        self.bind = bind
        self.port = port
        self._get_status = get_status
        self._token = token

        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _make_handler(self):
        token = self._token
        get_status = self._get_status

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args):  # noqa: N802
                # Quiet by default
                return

            def _json(self, code: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth_ok(self) -> bool:
                if not token:
                    return True
                auth = self.headers.get("Authorization", "")
                return auth.strip() == f"Bearer {token}"

            def do_GET(self):  # noqa: N802
                path = (self.path or "").split("?", 1)[0]
                if path == "/health":
                    self._json(200, {"ok": True})
                    return

                if path == "/status":
                    if not self._auth_ok():
                        self._json(401, {"ok": False, "error": "unauthorized"})
                        return
                    st = get_status()
                    self._json(
                        200,
                        {
                            "ok": True,
                            "uptime_s": int(time.time() - st.started_at),
                            "telegram_enabled": st.telegram_enabled,
                            "active_tasks": st.active_tasks,
                            "jobs_enabled": st.jobs_enabled,
                        },
                    )
                    return

                self._json(404, {"ok": False, "error": "not_found"})

        return Handler

    def start(self) -> None:
        handler = self._make_handler()
        self._httpd = ThreadingHTTPServer((self.bind, self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
        self._httpd = None
        self._thread = None

