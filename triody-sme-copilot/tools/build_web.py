"""Build the browser version in webapp/ from the sources in triody/ and data/.

    python tools/build_web.py

Run it again after any change to triody/ or data/, then commit webapp/.
webapp/pyodide/ holds an unmodified copy of Pyodide and is not touched by this script.
"""
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_WEB = ROOT / "triody" / "web"
OUT = ROOT / "webapp"
BUNDLE = OUT / "triody_bundle.zip"
FIXED_TIME = (2026, 10, 14, 0, 0, 0)

# Browser build: no server, so connect-src only needs the same site. 'wasm-unsafe-eval' lets the
# browser compile Pyodide's WebAssembly; plain 'unsafe-eval' is not granted.
CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
       "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'")

NOTICE = ('<p class="notice">Runs fully in your browser. No data leaves this device. '
          'The shop and its numbers are synthetic. Reloading the page starts over.</p>')


def bundle_members():
    """(archive name, source path) for every file the browser needs."""
    out = []
    for p in sorted((ROOT / "triody").rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_file() and "__pycache__" not in rel.parts and "web" not in rel.parts \
                and p.suffix in (".py", ".json"):
            out.append((rel.as_posix(), p))
    for p in sorted((ROOT / "data").iterdir()):
        if p.is_file():
            out.append((f"data/{p.name}", p))
    for p in sorted((ROOT / "data" / "shop_d").iterdir()):   # the Shenzhen demo shop, opened with ?shop=d
        if p.is_file():
            out.append((f"data/shop_d/{p.name}", p))
    return out


def build_bundle():
    with zipfile.ZipFile(BUNDLE, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in bundle_members():
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())


def build_index():
    html = (SRC_WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/style.css"', 'href="style.css"')
    html = html.replace('<meta charset="utf-8">',
                        f'<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="{CSP}">')
    html = html.replace('<script src="/app.js"></script>',
                        '<script src="pyodide/pyodide.js"></script>\n<script src="boot.js"></script>\n'
                        '<script src="app.js"></script>')
    html = html.replace("</header>", f"  {NOTICE}\n</header>")
    for needed in ('href="style.css"', 'src="boot.js"', "Content-Security-Policy"):
        if needed not in html:
            sys.exit(f"index.html changed shape; could not insert {needed}")
    (OUT / "index.html").write_text(html, encoding="utf-8")


def main():
    if not (OUT / "pyodide" / "pyodide.js").exists():
        sys.exit("webapp/pyodide/ is missing. See webapp/pyodide/NOTICE.txt for how to restore it.")
    for name in ("app.js", "boot.js", "style.css"):
        shutil.copyfile(SRC_WEB / name, OUT / name)
    build_index()
    build_bundle()
    print(f"built {OUT} ({len(bundle_members())} files in the bundle)")


if __name__ == "__main__":
    main()
