"""Local web server for the owner dashboard. Standard library only.

Safe defaults: binds to 127.0.0.1, serves three fixed files, requires a custom header on POST,
limits the request size and sends a strict Content-Security-Policy.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import webapi

WEB = Path(__file__).with_name("web")
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_BODY = webapi.MAX_BODY
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def make_handler(copilot):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Triody"

        def log_message(self, *args):
            pass

        def _send(self, status, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            """Refuse requests whose Host header is not local. Blocks DNS-rebinding pages."""
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost", "[::1]")

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "host not allowed"})
            path = self.path.split("?")[0]
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path in FILES:
                name, ctype = FILES[path]
                return self._send(200, (WEB / name).read_bytes(), ctype)
            if path.startswith("/api/"):
                with lock:
                    status, payload = webapi.dispatch(copilot, "GET", path)
                return self._send(status, payload)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "host not allowed"})
            if self.headers.get("X-Triody-Client") != "web":
                return self._send(403, {"error": "missing client header"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400, {"error": "bad length"})
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"error": "body size not allowed"})
            raw = self.rfile.read(length)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return self._send(400, {"error": "invalid json"})
            with lock:
                status, payload = webapi.dispatch(copilot, "POST", self.path, text)
            self._send(status, payload)

    return Handler


def serve(copilot, host="127.0.0.1", port=8000):
    httpd = ThreadingHTTPServer((host, port), make_handler(copilot))
    print(f"Triody dashboard on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
