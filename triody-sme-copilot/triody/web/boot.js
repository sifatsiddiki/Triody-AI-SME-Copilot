"use strict";
// Browser build only. Loads Pyodide (CPython compiled to WebAssembly) from the same site,
// unpacks the triody package and the synthetic ledger into the page's in-memory file system,
// and hands app.js a function that calls the same Python request handler the local server uses.
// Nothing is sent to any server: every calculation runs on the visitor's device.
window.TriodyReady = (async () => {
  const alertBox = document.getElementById("alert");
  if (typeof WebAssembly !== "object") {
    throw new Error("this browser does not support WebAssembly. Update the browser or use a newer device.");
  }
  alertBox.textContent = "Loading the Python engine in your browser (about 5 MB, faster after the first visit)...";
  const base = document.baseURI;
  // ?shop=d opens the Shenzhen tea shop with the China catalogue. Anything else opens the UK café.
  const shopD = new URLSearchParams(location.search).get("shop") === "d";
  const dataDir = shopD ? "/home/pyodide/app/data/shop_d" : "/home/pyodide/app/data";
  window.TriodyShops = [
    { region: "UK", label: "Switch to the UK café demo (UK catalogue, GBP)", href: "?" },
    { region: "CN", label: "Switch to the Shenzhen tea shop demo (China catalogue, CNY)", href: "?shop=d" },
  ];
  const py = await loadPyodide({ indexURL: new URL("pyodide/", base).href });
  alertBox.textContent = "Loading the shop data...";
  const resp = await fetch(new URL("triody_bundle.zip", base));
  if (!resp.ok) throw new Error("could not load triody_bundle.zip (" + resp.status + ")");
  py.unpackArchive(await resp.arrayBuffer(), "zip", { extractDir: "/home/pyodide/app" });
  alertBox.textContent = "Running the 60-day cash forecast...";
  await new Promise((r) => setTimeout(r, 30));
  py.runPython(`
import sys
sys.path.insert(0, "/home/pyodide/app")
from triody.agent import Copilot
from triody.ledger import Ledger
from triody import webapi
_copilot = Copilot(Ledger.from_dir("${dataDir}"),
                   audit_path="/tmp/triody/audit_log.jsonl", sandbox_dir="/tmp/triody/sandbox")
def _triody_call(method, path, body):
    return webapi.dispatch_json(_copilot, method, path, body)
`);
  const call = py.globals.get("_triody_call");
  return async (path, body) => {
    // Let the browser paint the "you" message before Python takes the main thread.
    await new Promise((r) => setTimeout(r, 20));
    const out = JSON.parse(call(body === undefined ? "GET" : "POST", path, body === undefined ? "" : JSON.stringify(body)));
    return out.body;
  };
})();
