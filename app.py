"""Localhost-only dashboard for lab GPU server occupancy.

Run with: python3 app.py  (then open http://127.0.0.1:8787)
No third-party dependencies.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from collector import StatusCache

BIND = "127.0.0.1"  # private tool: never expose beyond this machine
PORT = 8787
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

cache = StatusCache()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            self._serve_index()
        elif self.path.startswith("/api/status"):
            self._serve_json(cache.snapshot())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/refresh":
            self._serve_json(cache.refresh_now())
        else:
            self.send_error(404)

    def _serve_index(self):
        with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet; errors still raise


def main():
    cache.run_forever()
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"Serving on http://{BIND}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
