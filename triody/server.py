"""Local web server for the owner dashboard. Standard library only.

Safe defaults: binds to 127.0.0.1, serves a fixed list of files, requires a custom header on POST,
limits the request size and sends a strict Content-Security-Policy.

With lan=True it also listens on the local network, so a phone or tablet on the same Wi-Fi can open
the dashboard. Those devices sign in once with the pairing code shown on the computer (see access.py).
The computer itself never needs to sign in. Host headers must be localhost or a plain IP address,
which blocks DNS-rebinding pages, and every API call from another device needs a valid session.
"""
import ipaddress
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import webapi
from .access import COOKIE, Access, cookie_value

WEB = Path(__file__).with_name("web")
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8"), "/icon.svg": ("icon.svg", "image/svg+xml")}
MAX_BODY = webapi.MAX_BODY
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
       "manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
LOCAL_NAMES = ("127.0.0.1", "localhost", "[::1]", "::1")


def lan_addresses() -> list:
    """This computer's addresses on the local network. Works without internet access: connecting a
    UDP socket sends no packet, it only asks the system which interface it would use."""
    found = []
    for probe in ("10.255.255.255", "192.168.255.255", "172.31.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 1))
                ip = s.getsockname()[0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
        except OSError:
            continue
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def make_handler(copilot, access: Access = None, lan: bool = False, port: int = 8000):
    lock = threading.Lock()
    access = access or Access()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Triody"
        sys_version = ""

        def log_message(self, *args):
            pass

        def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        # -- who is asking ---------------------------------------------------------------------
        def _client(self) -> str:
            return self.client_address[0]

        def _is_local(self) -> bool:
            ip = self._client()
            return ip in ("127.0.0.1", "::1") or ip.startswith("::ffff:127.") or ip.startswith("127.")

        def _host_ok(self) -> bool:
            """Refuse Host headers that are not local or a plain IP. Blocks DNS-rebinding pages."""
            raw = (self.headers.get("Host") or "").strip().lower()
            host = raw[: raw.find("]") + 1] if raw.startswith("[") else raw.split(":")[0]
            if host in LOCAL_NAMES:
                return True
            return lan and _is_ip(host)

        def _token(self):
            return cookie_value(self.headers.get("Cookie"))

        def _signed_in(self) -> bool:
            return self._is_local() or access.valid(self._token())

        def _read_json(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None, (400, {"error": "bad length"})
            if length <= 0 or length > MAX_BODY:
                return None, (413, {"error": "body size not allowed"})
            raw = self.rfile.read(length)
            try:
                return raw.decode("utf-8"), None
            except UnicodeDecodeError:
                return None, (400, {"error": "invalid json"})

        # -- GET -------------------------------------------------------------------------------
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
            if path == "/api/session":
                return self._send(200, {"signed_in": self._signed_in(), "this_computer": self._is_local(),
                                        "lan": lan})
            if path == "/api/pairing":
                if not self._is_local():
                    return self._send(403, {"error": "only on the computer running Triody"})
                if not lan:
                    return self._send(200, {"lan": False})
                info = access.pairing_code()
                return self._send(200, {"lan": True, **info, "devices": access.devices(),
                                        "urls": [f"http://{ip}:{port}" for ip in lan_addresses()]})
            if path.startswith("/api/"):
                if not self._signed_in():
                    return self._send(401, {"error": "sign_in", "text": "Enter the pairing code shown on the "
                                                                        "computer that runs Triody."})
                with lock:
                    status, payload = webapi.dispatch(copilot, "GET", path)
                return self._send(status, payload)
            self._send(404, {"error": "not found"})

        # -- POST ------------------------------------------------------------------------------
        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "host not allowed"})
            if self.headers.get("X-Triody-Client") != "web":
                return self._send(403, {"error": "missing client header"})
            text, err = self._read_json()
            if err:
                return self._send(*err)
            path = self.path.split("?")[0]
            if path == "/api/pair":
                try:
                    body = json.loads(text)
                except ValueError:
                    return self._send(400, {"error": "invalid json"})
                if not isinstance(body, dict) or not isinstance(body.get("code"), str):
                    return self._send(400, {"error": "invalid json"})
                res = access.pair(body["code"], self._client(), str(body.get("device", ""))[:60])
                if not res["ok"]:
                    with lock:
                        copilot.audit.append("system", "pairing_failed", client=self._client())
                    return self._send(401, {"error": "pairing", "text": res["error"]})
                with lock:
                    copilot.audit.append("system", "device_paired", client=self._client())
                cookie = f"{COOKIE}={res['token']}; Max-Age={res['max_age']}; Path=/; HttpOnly; SameSite=Strict"
                return self._send(200, {"signed_in": True}, extra={"Set-Cookie": cookie})
            if path == "/api/logout":
                access.sign_out(self._token())
                return self._send(200, {"signed_in": self._is_local()},
                                  extra={"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"})
            if path == "/api/logout_all":
                if not self._is_local():
                    return self._send(403, {"error": "only on the computer running Triody"})
                n = access.sign_out_all()
                with lock:
                    copilot.audit.append("owner", "devices_signed_out", count=n)
                return self._send(200, {"signed_out": n})
            if not self._signed_in():
                return self._send(401, {"error": "sign_in", "text": "Enter the pairing code shown on the "
                                                                    "computer that runs Triody."})
            with lock:
                status, payload = webapi.dispatch(copilot, "POST", path, text)
            self._send(status, payload)

        def do_OPTIONS(self):
            self._send(405, {"error": "method not allowed"})

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS

    return Handler


def serve(copilot, host="127.0.0.1", port=8000, lan=False, access=None):
    bind = "0.0.0.0" if lan else host
    access = access or Access()
    httpd = ThreadingHTTPServer((bind, port), make_handler(copilot, access, lan=lan, port=port))
    print(f"Triody dashboard on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    if lan:
        urls = [f"http://{ip}:{port}" for ip in lan_addresses()]
        if urls:
            print("On a phone or tablet on the same Wi-Fi, open " + " or ".join(urls))
            print(f"and enter the pairing code shown on this computer's dashboard "
                  f"(now: {access.pairing_code()['code']}).")
        else:
            print("No local network address found. Connect this computer to Wi-Fi to use other devices.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
