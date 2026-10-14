import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests.helpers import make_copilot
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


if __name__ == "__main__":
    unittest.main()
