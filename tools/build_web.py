"""Build the browser version in webapp/ from the sources in triody/ and data/.

    python tools/build_web.py

Run it again after any change to triody/ or data/, then commit webapp/.
webapp/pyodide/ holds an unmodified copy of Pyodide and is not touched by this script.

The browser version is also an installable app (a PWA): manifest.webmanifest names it and its icons,
and sw.js keeps every file in the browser's cache, so after the first visit it opens and works with
no internet connection on Android, iPhone and iPad, Windows, macOS, ChromeOS and Linux.
"""
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_WEB = ROOT / "triody" / "web"
OUT = ROOT / "webapp"
BUNDLE = OUT / "triody_bundle.zip"
FIXED_TIME = (2026, 10, 14, 0, 0, 0)
COPIED = ("app.js", "boot.js", "style.css", "icon.svg")
ICONS = ("icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png")
PYODIDE = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")

# Browser build: no server, so connect-src only needs the same site. 'wasm-unsafe-eval' lets the
# browser compile Pyodide's WebAssembly; plain 'unsafe-eval' is not granted.
CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
       "connect-src 'self'; img-src 'self'; worker-src 'self'; manifest-src 'self'; "
       "base-uri 'none'; form-action 'none'; object-src 'none'")

NOTICE = ('<p class="notice">Runs fully on your device, with or without internet. No data leaves this device. '
          'The shop and its numbers are synthetic. Reloading the page starts over.</p>')

HEAD_EXTRA = ('<link rel="manifest" href="manifest.webmanifest">\n'
              '<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">\n'
              '<meta name="apple-mobile-web-app-capable" content="yes">\n'
              '<meta name="mobile-web-app-capable" content="yes">\n'
              '<meta name="apple-mobile-web-app-title" content="Triody">\n'
              '<meta name="apple-mobile-web-app-status-bar-style" content="default">\n')

MANIFEST = {
    "name": "Triody AI",
    "short_name": "Triody",
    "description": "Cash forecast, figures and financing for a small business. Runs on your own device, "
                   "also offline.",
    "id": "./",
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#f6f7f9",
    "theme_color": "#1d5fd0",
    "categories": ["finance", "business", "productivity"],
    "icons": [
        {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    ],
}


def bundle_members():
    """(archive name, source path) for every file the browser needs."""
    out = []
    for p in sorted((ROOT / "triody").rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_file() and "__pycache__" not in rel.parts and "web" not in rel.parts \
                and p.suffix in (".py", ".json"):
            out.append((rel.as_posix(), p))
    for folder in (ROOT / "data", ROOT / "data" / "shop_d"):   # shop_d is the Shenzhen demo, opened with ?shop=d
        for p in sorted(folder.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                out.append((p.relative_to(ROOT).as_posix(), p))
    return out


def build_bundle():
    with zipfile.ZipFile(BUNDLE, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in bundle_members():
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())


def build_index():
    html = (SRC_WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('="/', '="')            # every local link becomes relative to webapp/
    html = html.replace('<meta charset="utf-8">',
                        f'<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="{CSP}">')
    html = html.replace('<link rel="stylesheet"', HEAD_EXTRA + '<link rel="stylesheet"')
    html = html.replace('<script src="app.js"></script>',
                        '<script src="pyodide/pyodide.js"></script>\n<script src="boot.js"></script>\n'
                        '<script src="app.js"></script>')
    html = html.replace("</header>", f"  {NOTICE}\n</header>")
    for needed in ('href="style.css"', 'src="boot.js"', "Content-Security-Policy", 'rel="manifest"'):
        if needed not in html:
            sys.exit(f"index.html changed shape; could not insert {needed}")
    (OUT / "index.html").write_text(html, encoding="utf-8")


def cached_files() -> list:
    """Every file the app needs offline, as paths relative to webapp/."""
    files = ["./", "index.html", "manifest.webmanifest", "triody_bundle.zip", *COPIED]
    files += [f"icons/{n}" for n in ICONS] + [f"pyodide/{n}" for n in PYODIDE]
    return files


def build_service_worker():
    files = cached_files()
    digest = hashlib.sha256()
    for f in files[1:]:
        digest.update(f.encode())
        digest.update((OUT / f).read_bytes())
    sw = (SRC_WEB / "sw.js").read_text(encoding="utf-8")
    sw = sw.replace('"__VERSION__"', json.dumps(digest.hexdigest()[:12]))
    sw = sw.replace("__FILES__", json.dumps(files))
    (OUT / "sw.js").write_text(sw, encoding="utf-8")


def main():
    if not (OUT / "pyodide" / "pyodide.js").exists():
        sys.exit("webapp/pyodide/ is missing. See webapp/pyodide/NOTICE.txt for how to restore it.")
    for name in COPIED:
        shutil.copyfile(SRC_WEB / name, OUT / name)
    (OUT / "icons").mkdir(exist_ok=True)
    for name in ICONS:
        shutil.copyfile(SRC_WEB / "icons" / name, OUT / "icons" / name)
    (OUT / "manifest.webmanifest").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
    build_index()
    build_bundle()
    build_service_worker()
    print(f"built {OUT} ({len(bundle_members())} files in the bundle, {len(cached_files())} kept for offline use)")


if __name__ == "__main__":
    main()
