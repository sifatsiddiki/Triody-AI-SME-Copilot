"""Checks for the browser build: shared request handler and an up-to-date bundle."""
import json
import shutil
import unittest
import zipfile
from pathlib import Path

from tests.helpers import make_copilot
from triody import webapi

ROOT = Path(__file__).resolve().parent.parent
WEBAPP = ROOT / "webapp"


def _norm(b: bytes) -> bytes:
    """Ignore Windows line endings that git may add on checkout."""
    return b.replace(b"\r\n", b"\n")


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.c, self.tmp = make_copilot()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_state_and_audit(self):
        status, body = webapi.dispatch(self.c, "GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("forecast", body)
        status, body = webapi.dispatch(self.c, "GET", "/api/audit")
        self.assertTrue(body["chain"]["ok"])

    def test_bad_requests(self):
        self.assertEqual(webapi.dispatch(self.c, "GET", "/api/nope")[0], 404)
        self.assertEqual(webapi.dispatch(self.c, "PUT", "/api/chat", "{}")[0], 405)
        self.assertEqual(webapi.dispatch(self.c, "POST", "/api/chat", "")[0], 413)
        self.assertEqual(webapi.dispatch(self.c, "POST", "/api/chat", "x" * 5000)[0], 413)
        self.assertEqual(webapi.dispatch(self.c, "POST", "/api/chat", "not json")[0], 400)
        self.assertEqual(webapi.dispatch(self.c, "POST", "/api/chat", "[1]")[0], 400)

    def test_transfer_refused_through_browser_entry(self):
        out = json.loads(webapi.dispatch_json(self.c, "POST", "/api/chat",
                                              json.dumps({"message": "transfer £9,000 to account 12345678"})))
        self.assertEqual(out["status"], 200)
        self.assertIn("never allowed", out["body"]["text"])


@unittest.skipUnless((WEBAPP / "triody_bundle.zip").exists(), "webapp not built")
class TestBundleFresh(unittest.TestCase):
    def test_bundle_matches_sources(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_web", ROOT / "tools" / "build_web.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        members = build.bundle_members()
        with zipfile.ZipFile(WEBAPP / "triody_bundle.zip") as z:
            self.assertEqual(sorted(z.namelist()), sorted(n for n, _ in members))
            for name, path in members:
                self.assertEqual(_norm(z.read(name)), _norm(path.read_bytes()), f"{name} is stale: run python tools/build_web.py")
        for name in ("app.js", "boot.js", "style.css", "icon.svg"):
            self.assertEqual(_norm((WEBAPP / name).read_bytes()), _norm((ROOT / "triody" / "web" / name).read_bytes()),
                             f"webapp/{name} is stale: run python tools/build_web.py")


@unittest.skipUnless((WEBAPP / "sw.js").exists(), "webapp not built")
class TestOfflineApp(unittest.TestCase):
    """The browser build must install as an app and open with no connection."""

    def test_manifest_is_installable(self):
        m = json.loads((WEBAPP / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(m["display"], "standalone")
        sizes = {i["sizes"] for i in m["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        self.assertTrue(any(i.get("purpose") == "maskable" for i in m["icons"]))
        for icon in m["icons"]:
            self.assertTrue((WEBAPP / icon["src"]).exists(), icon["src"])

    def test_service_worker_caches_every_file_the_app_loads(self):
        sw = (WEBAPP / "sw.js").read_text(encoding="utf-8")
        self.assertNotIn("__FILES__", sw)
        files = json.loads(sw.split("const FILES = ")[1].split(";")[0])
        for f in files:
            if f != "./":
                self.assertTrue((WEBAPP / f).exists(), f)
        for needed in ("pyodide/pyodide.asm.wasm", "pyodide/python_stdlib.zip", "triody_bundle.zip", "app.js"):
            self.assertIn(needed, files)

    def test_page_links_manifest_and_ios_icon(self):
        html = (WEBAPP / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="manifest"', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn("worker-src 'self'", html)
        self.assertNotIn('="/', html)          # every link relative, so the app works from any folder
