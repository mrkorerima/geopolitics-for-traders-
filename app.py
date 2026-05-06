from __future__ import annotations

import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from market_monitor import CACHE_TTL_SECONDS, MarketMonitor


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
MONITOR = MarketMonitor()


class RadarHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "cacheSeconds": CACHE_TTL_SECONDS,
                    "service": "gold-oil-geopolitics-radar",
                }
            )
            return

        if parsed.path == "/api/monitor":
            query = parse_qs(parsed.query)
            force = query.get("force", ["0"])[0] in {"1", "true", "yes"}
            payload = MONITOR.get_snapshot(force=force)
            self._send_json(payload)
            return

        if parsed.path in {"/", ""}:
            self.path = "/index.html"

        super().do_GET()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return

    def end_headers(self) -> None:
        self._send_cors_headers()
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")

    def _send_json(self, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            return


def main() -> None:
    host = "127.0.0.1"
    port = 8000
    server = ThreadingHTTPServer((host, port), RadarHandler)

    print(f"Gold & Oil Geopolitics Radar running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
