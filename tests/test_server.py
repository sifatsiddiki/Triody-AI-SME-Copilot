import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests.helpers import make_copilot
from triody.access import Access
from triody.server import make_handler


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.copilot, _ = make_copilot()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.copilot))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _req(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_index_has_security_headers(self):
        status, headers, _ = self._req("/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_post_requires_client_header(self):
        status, _, _ = self._req("/api/chat", {"message": "hi"}, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)

    def test_oversized_body_is_refused(self):
        status, _, _ = self._req("/api/chat", {"message": "x" * 5000},
                                 {"Content-Type": "application/json", "X-Triody-Client": "web"})
        self.assertEqual(status, 413)

    def test_unknown_path(self):
        self.assertEqual(self._req("/etc/passwd")[0], 404)
        self.assertEqual(self._req("/../triody/agent.py")[0], 404)

    def test_chat_round_trip(self):
        status, _, body = self._req("/api/chat", {"message": "Will I run out of cash?"},
                                    {"Content-Type": "application/json", "X-Triody-Client": "web"})
        self.assertEqual(status, 200)
        self.assertIn("Cash alert", json.loads(body)["text"])

    def test_foreign_host_header_is_refused(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/state", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)

    def test_non_object_json_is_refused(self):
        status, _, _ = self._req("/api/chat", [1, 2], {"Content-Type": "application/json", "X-Triody-Client": "web"})
        self.assertEqual(status, 400)


class FakeClock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class PairingTests(unittest.TestCase):
    """Phones on the same Wi-Fi sign in once with a 6-digit code. No passwords."""

    def setUp(self):
        self.clock = FakeClock()
        self.access = Access(clock=self.clock)

    def test_right_code_gives_a_session_once(self):
        code = self.access.pairing_code()["code"]
        res = self.access.pair(code, "10.0.0.5")
        self.assertTrue(res["ok"])
        self.assertTrue(self.access.valid(res["token"]))
        self.assertFalse(self.access.pair(code, "10.0.0.6")["ok"])        # one use only

    def test_wrong_codes_lock_the_client(self):
        code = self.access.pairing_code()["code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertFalse(self.access.pair(wrong, "10.0.0.9")["ok"])
        res = self.access.pair(code, "10.0.0.9")
        self.assertFalse(res["ok"])
        self.assertIn("Too many", res["error"])

    def test_code_expires(self):
        code = self.access.pairing_code()["code"]
        self.clock.t += 601
        self.assertFalse(self.access.pair(code, "10.0.0.5")["ok"])

    def test_session_expires_and_sign_out_works(self):
        res = self.access.pair(self.access.pairing_code()["code"], "10.0.0.5")
        self.assertTrue(self.access.sign_out(res["token"]))
        self.assertFalse(self.access.valid(res["token"]))
        res = self.access.pair(self.access.pairing_code()["code"], "10.0.0.5")
        self.clock.t += 31 * 24 * 3600
        self.assertFalse(self.access.valid(res["token"]))

    def test_store_keeps_hashes_not_tokens(self):
        import os
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "sessions.json")
        a = Access(path, clock=self.clock)
        token = a.pair(a.pairing_code()["code"], "10.0.0.5")["token"]
        with open(path, encoding="utf-8") as f:
            self.assertNotIn(token, f.read())
        self.assertTrue(Access(path, clock=self.clock).valid(token))   # survives a restart


class RemoteDeviceTests(unittest.TestCase):
    """A LAN server: requests that do not come from 127.0.0.1 need a session."""

    @classmethod
    def setUpClass(cls):
        cls.copilot, _ = make_copilot()
        cls.access = Access()
        handler = make_handler(cls.copilot, cls.access, lan=True)
        # Pretend every client is remote, so the tests can run on one machine.
        handler._is_local = lambda self: self.headers.get("X-Test-Local") == "1"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _req(self, path, body=None, headers=None):
        h = {"X-Triody-Client": "web", "Content-Type": "application/json", **(headers or {})}
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, e.headers, json.loads(e.read() or b"{}")

    def test_remote_device_must_pair(self):
        status, _, body = self._req("/api/state")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "sign_in")
        self.assertEqual(self._req("/api/chat", {"message": "sales"})[0], 401)

    def test_pairing_flow_sets_an_httponly_cookie(self):
        code = self.access.pairing_code()["code"]
        status, headers, _ = self._req("/api/pair", {"code": code})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        token = cookie.split(";")[0]
        status, _, body = self._req("/api/chat", {"message": "sales today"}, {"Cookie": token})
        self.assertEqual(status, 200)
        self.assertIn("Sales today", body["text"])

    def test_wrong_code_is_refused_and_logged(self):
        status, _, body = self._req("/api/pair", {"code": "12345x"})
        self.assertEqual(status, 401)
        self.assertIn("pairing_failed", [e["event"] for e in self.copilot.audit.entries()])

    def test_pairing_code_is_shown_only_on_the_computer(self):
        self.assertEqual(self._req("/api/pairing")[0], 403)
        status, _, body = self._req("/api/pairing", headers={"X-Test-Local": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["code"]), 6)

    def test_ip_host_is_allowed_on_lan_but_names_are_not(self):
        self.assertEqual(self._req("/api/session", headers={"Host": "192.168.1.20:8000"})[0], 200)
        self.assertEqual(self._req("/api/session", headers={"Host": "evil.example"})[0], 403)


if __name__ == "__main__":
    unittest.main()
