"use strict";
const $ = (id) => document.getElementById(id);
let SYMBOL = "£";
const gbp = (x) => (x < 0 ? "-" : "") + SYMBOL + Math.abs(Math.round(x)).toLocaleString("en-GB");
const NS = "http://www.w3.org/2000/svg";

function el(name, attrs) {
  const e = document.createElementNS(NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

// Browser storage can be missing or blocked (private windows, strict settings). Every read and
// write goes through these two helpers so the app keeps working without it.
const store = {
  get(key) { try { return JSON.parse(localStorage.getItem("triody." + key)); } catch (e) { return null; } },
  set(key, value) {
    try {
      if (value === null) localStorage.removeItem("triody." + key);
      else localStorage.setItem("triody." + key, JSON.stringify(value));
      return true;
    } catch (e) { return false; }
  },
};

// The browser build (webapp/) sets window.TriodyReady to a promise that resolves to a function
// running the same Python request handler inside the page. The local server build leaves it unset.
let transport = null;
const OFFLINE_BUILD = () => transport !== null;

class SignInNeeded extends Error {}

async function api(path, body) {
  if (transport) return transport(path, body);
  const opts = body === undefined ? { credentials: "same-origin" } : {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Triody-Client": "web" },
    body: JSON.stringify(body),
  };
  let r;
  try {
    r = await fetch(path, opts);
  } catch (e) {
    throw new Error("Can't reach the computer running Triody. Check that it is on, on the same Wi-Fi, and that the dashboard is still running.");
  }
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty or not JSON */ }
  if (r.status === 401 && data.error === "sign_in") throw new SignInNeeded(data.text || "");
  if (!r.ok && !data.text) data.text = "That request failed (" + r.status + "). Nothing was changed.";
  return data;
}

// ----------------------------------------------------------------------------- drawing
function drawChart(fc) {
  const svg = $("chart");
  svg.replaceChildren();
  const W = 720, H = 300, L = 56, R = 12, T = 12, B = 28;
  const days = fc.days;
  const ys = days.flatMap((d) => [d.p10, d.p90]).concat([0, fc.buffer]);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const x = (i) => L + (i * (W - L - R)) / (days.length - 1);
  const y = (v) => T + ((hi - v) * (H - T - B)) / (hi - lo || 1);
  const upper = days.map((d, i) => `${x(i)},${y(d.p90)}`);
  const lower = days.map((d, i) => `${x(i)},${y(d.p10)}`).reverse();
  svg.appendChild(el("polygon", { points: upper.concat(lower).join(" "), fill: "var(--band)" }));
  svg.appendChild(el("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), stroke: "var(--bad)", "stroke-width": 1.5 }));
  svg.appendChild(el("line", { x1: L, x2: W - R, y1: y(fc.buffer), y2: y(fc.buffer), stroke: "var(--warn)", "stroke-dasharray": "5 4" }));
  svg.appendChild(el("polyline", { points: days.map((d, i) => `${x(i)},${y(d.p50)}`).join(" "), fill: "none", stroke: "var(--accent)", "stroke-width": 2.5 }));
  [hi, (hi + lo) / 2, lo].forEach((v) => {
    const t = el("text", { x: 4, y: y(v) + 4, "font-size": 13, fill: "var(--muted)" });
    t.textContent = gbp(v);
    svg.appendChild(t);
  });
  [0, Math.floor(days.length / 2), days.length - 1].forEach((i) => {
    const t = el("text", { x: x(i), y: H - 8, "font-size": 13, fill: "var(--muted)", "text-anchor": i === 0 ? "start" : i === days.length - 1 ? "end" : "middle" });
    t.textContent = days[i].date;
    svg.appendChild(t);
  });
}

function cell(tr, v) {
  const td = document.createElement("td");
  td.textContent = v;
  tr.appendChild(td);
}

function renderAnalysis(a) {
  const ul = $("findings");
  ul.replaceChildren();
  a.findings.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
  const mb = document.querySelector("#months tbody");
  mb.replaceChildren();
  a.months.forEach((m) => {
    const tr = document.createElement("tr");
    cell(tr, m.month + (m.full ? "" : " (part)"));
    cell(tr, gbp(m.sales));
    cell(tr, m.gross_margin_pct === null ? "-" : m.gross_margin_pct + "%");
    cell(tr, m.wages_pct_of_sales === null ? "-" : m.wages_pct_of_sales + "%");
    cell(tr, gbp(m.operating_profit));
    mb.appendChild(tr);
  });
  const pb = document.querySelector("#payees tbody");
  pb.replaceChildren();
  a.top_payees_90d.forEach((p) => {
    const tr = document.createElement("tr");
    [p.counterparty, p.category, gbp(p.amount), p.share_of_costs_pct + "%", gbp(p.per_100_of_sales)].forEach((v) => cell(tr, v));
    pb.appendChild(tr);
  });
  $("analysis-note").textContent = a.notes.join(" ");
}

function renderDevice(list) {
  const box = $("device");
  box.replaceChildren();
  if (!list.length) {
    const p = document.createElement("p");
    p.className = "muted"; p.textContent = "No codes waiting.";
    box.appendChild(p);
    return;
  }
  list.forEach((m) => {
    const d = document.createElement("div");
    d.className = "sms";
    const t = document.createElement("div");
    t.textContent = `Triody: ${m.summary}. Your one-time code is`;
    const c = document.createElement("strong");
    c.className = "code"; c.textContent = m.code;
    const e = document.createElement("div");
    e.className = "muted"; e.textContent = `Valid until ${m.expires.replace("T", " ").slice(0, 16)} UTC. Never share this code.`;
    d.append(t, c, e);
    box.appendChild(d);
  });
}

function renderState(s) {
  if (!s || !s.forecast) return;
  const f = s.forecast, sm = f.summary;
  SYMBOL = s.currency_symbol || "£";
  if (s.shop) $("shop").textContent = "Demo shop: " + s.shop + ", synthetic data.";
  const a = $("alert");
  a.className = "alert " + sm.alert_level;
  a.textContent = sm.alert_level === "ok" ? "Cash looks fine for the forecast period."
    : `Cash alert (${sm.alert_level}). Risk of dropping under your ${gbp(f.buffer)} buffer starts ${sm.first_risk_date || "later"}.`;
  if (window.TriodyShops) {
    const sw = $("switch");
    sw.replaceChildren();
    window.TriodyShops.forEach((x) => {
      if (x.region === s.region) return;
      const link = document.createElement("a");
      link.href = x.href; link.textContent = x.label;
      sw.appendChild(link);
    });
  }
  $("kpi-balance").textContent = gbp(s.balance.balance);
  $("kpi-low").textContent = `${gbp(sm.lowest_p50.balance)} on ${sm.lowest_p50.date}`;
  $("kpi-prob").textContent = Math.round(sm.probability_negative_any_day * 100) + "%";
  drawChart(f);
  renderPending(s.pending);
  renderDevice(s.owner_device || []);
  if (s.analysis) renderAnalysis(s.analysis);
  if (s.brain) $("brain").textContent = "Planner: " + s.brain + ". It can read and draft. It cannot move money.";
}

function addMsg(text, who) {
  const d = document.createElement("div");
  d.className = "msg " + who;
  d.textContent = text;
  $("chat").appendChild(d);
  $("chat").scrollTop = $("chat").scrollHeight;
}

function renderPending(list) {
  const box = $("pending");
  box.replaceChildren();
  list.forEach((p) => {
    const d = document.createElement("div");
    d.className = "pending";
    const prop = p.proposal;
    const t = document.createElement("div");
    if (prop.kind === "financing_application" && prop.tax_deferral) {
      t.textContent = `Draft ${prop.proposal_id}: ask ${prop.tax_authority || "the tax office"} to spread ${gbp(prop.amount)} of the tax bill over ${prop.term_months} months. Nothing has been sent.`;
    } else if (prop.kind === "financing_application") {
      t.textContent = `Draft ${prop.proposal_id}: ${prop.product}, ${gbp(prop.amount)} over ${prop.term_months} months. Nothing has been sent.`;
    } else {
      t.textContent = `Draft ${prop.proposal_id}: ask ${prop.counterparty} to be paid ${prop.days} days later. Nothing has been sent.`;
    }
    const row = document.createElement("form");
    row.className = "row";
    const code = document.createElement("input");
    code.autocomplete = "one-time-code"; code.maxLength = 12; code.placeholder = "Code from your owner device";
    code.setAttribute("aria-label", "Approval code for " + prop.proposal_id);
    const ok = document.createElement("button");
    ok.className = "primary"; ok.type = "submit"; ok.textContent = "Confirm";
    row.onsubmit = (e) => { e.preventDefault(); act("/api/confirm", { code: code.value }); };
    const no = document.createElement("button");
    no.type = "button"; no.textContent = "Cancel";
    no.onclick = () => act("/api/cancel", { proposal_id: prop.proposal_id });
    row.append(code, ok, no);
    d.append(t, row);
    box.appendChild(d);
  });
}

// ----------------------------------------------------------------------------- talking to the copilot
function addSuggestions(list) {
  if (!list || !list.length) return;
  const row = document.createElement("div");
  row.className = "suggest";
  list.forEach((q) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "small"; b.textContent = q;
    b.addEventListener("click", () => { row.remove(); send(q); });
    row.appendChild(b);
  });
  $("chat").appendChild(row);
  $("chat").scrollTop = $("chat").scrollHeight;
}

async function call(path, body) {
  setBusy(true);
  try {
    const r = await api(path, body);
    if (r.text) addMsg(r.text, "bot");
    addSuggestions(r.suggestions);
    renderState(r.state);
    loadAudit();
  } catch (err) {
    if (err instanceof SignInNeeded) return showPairing(err.message);
    addMsg(err.message || "Something went wrong. Nothing was changed.", "bot error");
  } finally {
    setBusy(false);
  }
}

const act = (path, body) => call(path, body);

function send(text) {
  if (!text.trim()) return;
  addMsg(text, "you");
  call("/api/chat", { message: text });
}

async function loadAudit() {
  let r;
  try { r = await api("/api/audit"); } catch (e) { return; }
  if (!r || !r.chain) return;
  $("chain").textContent = r.chain.ok ? `Chain check passed: ${r.chain.entries} entries, none altered.`
    : `Chain check FAILED at entry ${r.chain.first_bad_seq}.`;
  const body = document.querySelector("#audit tbody");
  body.replaceChildren();
  r.entries.slice().reverse().forEach((e) => {
    const tr = document.createElement("tr");
    const detail = e.data.tool || e.data.reason || e.data.text || e.data.proposal || e.data.anchor || "";
    [e.seq, e.ts.slice(11, 19), e.actor, e.event, String(detail).slice(0, 80)].forEach((v) => cell(tr, v));
    body.appendChild(tr);
  });
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("msg").value;
  $("msg").value = "";
  send(v);
});
document.querySelectorAll(".quick button").forEach((b) => b.addEventListener("click", () => send(b.dataset.q)));

function setBusy(busy) {
  document.querySelectorAll("main button, main input").forEach((b) => { b.disabled = busy; });
}

// ----------------------------------------------------------------------------- connection status
function showNet() {
  const chip = $("net");
  if (navigator.onLine) {
    chip.textContent = OFFLINE_BUILD() ? "Online" : "Connected";
    chip.className = "chip ok";
  } else {
    chip.textContent = OFFLINE_BUILD() ? "Offline: still works" : "Offline";
    chip.className = "chip " + (OFFLINE_BUILD() ? "ok" : "bad");
  }
}
window.addEventListener("online", showNet);
window.addEventListener("offline", showNet);

// ----------------------------------------------------------------------------- install as an app
let installEvent = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installEvent = e;
  $("install").hidden = false;
});
window.addEventListener("appinstalled", () => { $("install").hidden = true; });
$("install").addEventListener("click", async () => {
  if (!installEvent) return;
  installEvent.prompt();
  await installEvent.userChoice.catch(() => null);
  installEvent = null;
  $("install").hidden = true;
});

function installHint() {
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (standalone || !OFFLINE_BUILD()) return "";
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) {
    return "To install on iPhone or iPad: tap Share, then Add to Home Screen.";
  }
  if (/Macintosh/.test(ua) && /Safari/.test(ua) && !/Chrome|Edg/.test(ua)) {
    return "To install on a Mac: in Safari choose File, then Add to Dock.";
  }
  return "To install: use Install app above, or your browser menu, then Install or Add to Home screen.";
}

// ----------------------------------------------------------------------------- the sign-in gate
const gate = {
  mode: null,
  show(mode, text) {
    this.mode = mode;
    $("gate").hidden = false;
    document.body.classList.add("gated");
    $("gate-text").textContent = text;
    $("gate-error").textContent = "";
    $("gate-form").hidden = !(mode === "pair" || mode === "pin" || mode === "pin-new");
    $("gate-unlock").hidden = mode !== "passkey";
    $("gate-reset").hidden = !(mode === "passkey" || mode === "pin");
    const input = $("gate-input");
    input.value = "";
    input.maxLength = 6;
    input.placeholder = mode === "pair" ? "6-digit code" : "PIN (4 to 6 digits)";
    input.type = mode === "pair" ? "text" : "password";
    if (!$("gate-form").hidden) setTimeout(() => input.focus(), 50);
    else setTimeout(() => $("gate-unlock").focus(), 50);
  },
  hide() {
    $("gate").hidden = true;
    document.body.classList.remove("gated");
    this.mode = null;
  },
  error(text) { $("gate-error").textContent = text; },
};

let afterSignIn = null;

function showPairing(text) {
  gate.show("pair", text || "Enter the 6-digit pairing code shown on the computer that runs Triody. You only need to do this once on this device.");
}

$("gate-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const v = $("gate-input").value.trim();
  if (gate.mode === "pair") return pairDevice(v);
  if (gate.mode === "pin") return deviceLock.tryPin(v);
  if (gate.mode === "pin-new") return deviceLock.savePin(v);
});
$("gate-unlock").addEventListener("click", () => deviceLock.tryPasskey());
$("gate-reset").addEventListener("click", () => deviceLock.reset());

async function pairDevice(code) {
  if (!/^\d{6}$/.test(code)) return gate.error("The code has 6 digits.");
  $("gate-submit").disabled = true;
  try {
    const r = await fetch("/api/pair", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Triody-Client": "web" },
      body: JSON.stringify({ code, device: (navigator.platform || "device").slice(0, 40) }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return gate.error(data.text || "That did not work. Try again.");
    gate.hide();
    if (afterSignIn) afterSignIn();
  } catch (err) {
    gate.error("Can't reach the computer running Triody. Check the Wi-Fi.");
  } finally {
    $("gate-submit").disabled = false;
  }
}

// ----------------------------------------------------------------------------- device lock (browser build)
// The browser build keeps everything on this device, so there is nothing to sign in to: it opens
// straight away. The owner can add a lock that uses the device's own Face ID, fingerprint or
// Windows Hello (a passkey), or a PIN where passkeys are not available. The passkey's signature is
// checked here with the public key saved when the lock was set up. Resetting the lock also clears
// the session, so it gives nothing away.
const b64 = {
  enc(buf) { return btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); },
  dec(s) {
    const t = s.replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(t + "===".slice((t.length + 3) % 4)), (c) => c.charCodeAt(0));
  },
};
const randomBytes = (n) => crypto.getRandomValues(new Uint8Array(n));

function derToRaw(der) {
  // ECDSA signatures arrive DER-encoded; WebCrypto wants r and s as two 32-byte numbers.
  const d = new Uint8Array(der);
  let i = 2;
  if (d[1] & 0x80) i += d[1] & 0x7f;
  const read = () => {
    if (d[i++] !== 0x02) throw new Error("bad signature");
    const len = d[i++];
    let v = d.slice(i, i + len);
    i += len;
    while (v.length > 32 && v[0] === 0) v = v.slice(1);
    const out = new Uint8Array(32);
    out.set(v, 32 - v.length);
    return out;
  };
  const r = read(), s = read();
  const raw = new Uint8Array(64);
  raw.set(r, 0); raw.set(s, 32);
  return raw;
}

const deviceLock = {
  IDLE_MS: 10 * 60 * 1000,
  HIDDEN_MS: 5 * 60 * 1000,
  lastActive: Date.now(),
  hiddenAt: null,

  config() { return store.get("lock"); },
  secure() { return window.isSecureContext && !!(window.crypto && crypto.subtle); },
  async passkeyAvailable() {
    try {
      // Passkeys need a site name; a bare IP address such as 192.168.1.20 cannot hold one.
      if (/^[\d.]+$|:/.test(location.hostname)) return false;
      return this.secure() && !!window.PublicKeyCredential &&
        await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
    } catch (e) { return false; }
  },

  async refreshMenu() {
    const cfg = this.config();
    const canPasskey = await this.passkeyAvailable();
    $("lock-setup").hidden = !!cfg || !canPasskey;
    $("pin-setup").hidden = !!cfg || !this.secure();
    $("lock-now").hidden = !cfg;
    $("lock-remove").hidden = !cfg;
    $("sign-out").hidden = true;
    let status = "Signed in on this device. Your data stays on this device.";
    if (cfg && cfg.kind === "passkey") status += " Locked with Face ID, fingerprint or Windows Hello.";
    else if (cfg && cfg.kind === "pin") status += " Locked with a PIN.";
    else if (!this.secure()) status += " A lock needs the app to be opened over https.";
    else status += " Add a lock if other people use this device.";
    $("account-status").textContent = status;
  },

  async setupPasskey() {
    try {
      const cred = await navigator.credentials.create({ publicKey: {
        challenge: randomBytes(32),
        rp: { name: "Triody AI" },
        user: { id: randomBytes(16), name: "owner", displayName: "Shop owner" },
        pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
        authenticatorSelection: { authenticatorAttachment: "platform", userVerification: "required", residentKey: "discouraged" },
        timeout: 60000, attestation: "none",
      } });
      const resp = cred.response;
      const spki = typeof resp.getPublicKey === "function" ? resp.getPublicKey() : null;
      const alg = typeof resp.getPublicKeyAlgorithm === "function" ? resp.getPublicKeyAlgorithm() : -7;
      const saved = store.set("lock", { kind: "passkey", id: b64.enc(cred.rawId), spki: spki ? b64.enc(spki) : null, alg });
      addMsg(saved ? "Lock set. Next time, unlock with Face ID, fingerprint or Windows Hello."
        : "This browser does not allow saving settings, so the lock could not be kept.", "bot");
    } catch (e) {
      addMsg("The lock was not set up. You can try again from Account.", "bot");
    }
    this.refreshMenu();
  },

  async tryPasskey() {
    const cfg = this.config();
    const challenge = randomBytes(32);
    try {
      const a = await navigator.credentials.get({ publicKey: {
        challenge, allowCredentials: [{ type: "public-key", id: b64.dec(cfg.id) }],
        userVerification: "required", timeout: 60000,
      } });
      if (!(await this.verify(a, challenge, cfg))) return gate.error("That did not verify. Try again.");
      this.unlocked();
    } catch (e) {
      gate.error("Unlock was cancelled or timed out. Tap Unlock to try again.");
    }
  },

  async verify(assertion, challenge, cfg) {
    const r = assertion.response;
    const client = JSON.parse(new TextDecoder().decode(r.clientDataJSON));
    if (client.type !== "webauthn.get" || client.challenge !== b64.enc(challenge) || client.origin !== location.origin) return false;
    const auth = new Uint8Array(r.authenticatorData);
    if ((auth[32] & 0x05) !== 0x05) return false;            // user present and user verified
    if (!cfg.spki) return true;                              // older browsers: the platform check above is all we have
    const hash = await crypto.subtle.digest("SHA-256", r.clientDataJSON);
    const signed = new Uint8Array(auth.length + 32);
    signed.set(auth, 0); signed.set(new Uint8Array(hash), auth.length);
    if (cfg.alg === -257) {
      const key = await crypto.subtle.importKey("spki", b64.dec(cfg.spki), { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
      return crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, r.signature, signed);
    }
    const key = await crypto.subtle.importKey("spki", b64.dec(cfg.spki), { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    return crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, derToRaw(r.signature), signed);
  },

  async pinHash(pin, salt) {
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pin), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt, iterations: 310000, hash: "SHA-256" }, base, 256);
    return b64.enc(bits);
  },

  async savePin(pin) {
    if (!/^\d{4,6}$/.test(pin)) return gate.error("Use 4 to 6 digits.");
    const salt = randomBytes(16);
    const saved = store.set("lock", { kind: "pin", salt: b64.enc(salt), hash: await this.pinHash(pin, salt) });
    gate.hide();
    addMsg(saved ? "PIN set. You will need it when the app locks." : "This browser does not allow saving settings, so the PIN could not be kept.", "bot");
    this.refreshMenu();
  },

  async tryPin(pin) {
    const cfg = this.config();
    const tries = store.get("tries") || { n: 0, until: 0 };
    if (Date.now() < tries.until) {
      return gate.error(`Too many wrong PINs. Try again in ${Math.ceil((tries.until - Date.now()) / 1000)} seconds.`);
    }
    if (await this.pinHash(pin, b64.dec(cfg.salt)) === cfg.hash) {
      store.set("tries", null);
      return this.unlocked();
    }
    tries.n += 1;
    if (tries.n >= 5) tries.until = Date.now() + Math.min(15 * 60, 30 * 2 ** (tries.n - 5)) * 1000;
    store.set("tries", tries);
    gate.error(tries.n >= 5 ? "Wrong PIN. The lock now waits before the next try." : "Wrong PIN.");
  },

  lock() {
    const cfg = this.config();
    if (!cfg) return;
    if (cfg.kind === "passkey") gate.show("passkey", "Locked. Unlock with Face ID, fingerprint or Windows Hello.");
    else gate.show("pin", "Locked. Enter your PIN.");
  },

  unlocked() {
    gate.hide();
    this.lastActive = Date.now();
    if (afterSignIn) { const f = afterSignIn; afterSignIn = null; f(); }
  },

  reset() {
    if (!confirm("This removes the lock and starts a fresh session on this device. Continue?")) return;
    store.set("lock", null);
    store.set("tries", null);
    location.reload();
  },

  watch() {
    ["pointerdown", "keydown", "touchstart", "scroll"].forEach((ev) =>
      window.addEventListener(ev, () => { this.lastActive = Date.now(); }, { passive: true }));
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { this.hiddenAt = Date.now(); return; }
      if (this.hiddenAt && Date.now() - this.hiddenAt > this.HIDDEN_MS) this.lock();
      this.hiddenAt = null;
    });
    setInterval(() => {
      if (!gate.mode && this.config() && Date.now() - this.lastActive > this.IDLE_MS) this.lock();
    }, 30000);
  },
};

$("lock-setup").addEventListener("click", () => deviceLock.setupPasskey());
$("pin-setup").addEventListener("click", () => gate.show("pin-new", "Choose a PIN of 4 to 6 digits. You will need it to open Triody on this device."));
$("lock-now").addEventListener("click", () => { $("account-menu").hidden = true; deviceLock.lock(); });
$("lock-remove").addEventListener("click", () => {
  store.set("lock", null);
  store.set("tries", null);
  addMsg("Lock turned off on this device.", "bot");
  deviceLock.refreshMenu();
});
$("account").addEventListener("click", () => {
  const menu = $("account-menu");
  menu.hidden = !menu.hidden;
  if (!menu.hidden && OFFLINE_BUILD()) deviceLock.refreshMenu();
  $("account").setAttribute("aria-expanded", String(!menu.hidden));
  const hint = installHint();
  $("install-hint").hidden = !hint;
  $("install-hint").textContent = hint;
});
$("sign-out").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Triody-Client": "web" }, body: "{}" }).catch(() => null);
  location.reload();
});

// ----------------------------------------------------------------------------- server mode: other devices
async function serverAccount() {
  let s = { signed_in: true, this_computer: true, lan: false };
  try { s = await (await fetch("/api/session", { credentials: "same-origin" })).json(); } catch (e) { /* offline */ }
  $("lock-setup").hidden = true;
  $("pin-setup").hidden = true;
  $("sign-out").hidden = s.this_computer;
  $("account-status").textContent = s.this_computer
    ? "This is the computer running Triody, so no sign-in is needed here."
    : "This device is signed in with a pairing code. It stays signed in for 30 days.";
  if (s.this_computer && s.lan) showPairCard();
  return s;
}

async function showPairCard() {
  try {
    const p = await (await fetch("/api/pairing", { credentials: "same-origin" })).json();
    if (!p.lan) return;
    $("pair-card").hidden = false;
    $("pair-url").textContent = p.urls.length ? p.urls.join("  or  ") : "No Wi-Fi address found on this computer.";
    $("pair-code").textContent = p.code;
    $("pair-meta").textContent = `The code changes after each use and every 10 minutes. Devices signed in: ${p.devices}.`;
  } catch (e) { /* the card stays hidden */ }
}
$("pair-revoke").addEventListener("click", async () => {
  await fetch("/api/logout_all", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Triody-Client": "web" }, body: "{}" }).catch(() => null);
  showPairCard();
});

// ----------------------------------------------------------------------------- start
async function start() {
  renderState(await api("/api/state"));
  // The agent runs its own daily check without being asked, and may prepare a draft for approval.
  const brief = await api("/api/briefing", {});
  addMsg(brief.text, "bot");
  renderState(brief.state);
  await loadAudit();
  setBusy(false);
}

function startError(err) {
  if (err instanceof SignInNeeded) {
    afterSignIn = () => start().catch(startError);
    return showPairing(err.message);
  }
  const a = $("alert");
  a.className = "alert critical";
  a.textContent = "Could not start: " + (err && err.message ? err.message : String(err));
}

setBusy(true);
(window.TriodyReady || Promise.resolve(null)).then(async (t) => {
  transport = t;
  showNet();
  if (OFFLINE_BUILD()) {
    await deviceLock.refreshMenu();
    deviceLock.watch();
    if (deviceLock.config()) {
      afterSignIn = () => start().catch(startError);
      return deviceLock.lock();
    }
  } else {
    setInterval(() => { if (!$("pair-card").hidden) showPairCard(); }, 30000);
    await serverAccount();
  }
  await start();
}).catch(startError);
