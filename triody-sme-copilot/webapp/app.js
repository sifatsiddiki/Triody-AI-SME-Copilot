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

// The browser build (webapp/) sets window.TriodyReady to a promise that resolves to a function
// running the same Python request handler inside the page. The local server build leaves it unset.
let transport = null;

async function api(path, body) {
  if (transport) return transport(path, body);
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Triody-Client": "web" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  return r.json();
}

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
      const a = document.createElement("a");
      a.href = x.href; a.textContent = x.label;
      sw.appendChild(a);
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
    t.textContent = prop.kind === "financing_application"
      ? `Draft ${prop.proposal_id}: ${prop.product}, ${gbp(prop.amount)} over ${prop.term_months} months. Nothing has been sent.`
      : `Draft ${prop.proposal_id}: ask ${prop.counterparty} to be paid ${prop.days} days later. Nothing has been sent.`;
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

async function act(path, body) {
  setBusy(true);
  const r = await api(path, body).finally(() => setBusy(false));
  addMsg(r.text, "bot");
  renderState(r.state);
  loadAudit();
}

async function send(text) {
  if (!text.trim()) return;
  addMsg(text, "you");
  setBusy(true);
  const r = await api("/api/chat", { message: text }).finally(() => setBusy(false));
  addMsg(r.text, "bot");
  renderState(r.state);
  loadAudit();
}

async function loadAudit() {
  const r = await api("/api/audit");
  $("chain").textContent = r.chain.ok ? `Chain check passed: ${r.chain.entries} entries, none altered.`
    : `Chain check FAILED at entry ${r.chain.first_bad_seq}.`;
  const body = document.querySelector("#audit tbody");
  body.replaceChildren();
  r.entries.slice().reverse().forEach((e) => {
    const tr = document.createElement("tr");
    const detail = e.data.tool || e.data.reason || e.data.text || e.data.proposal || e.data.anchor || "";
    [e.seq, e.ts.slice(11, 19), e.actor, e.event, String(detail).slice(0, 80)].forEach((v) => {
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(td);
    });
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
  document.querySelectorAll("button, input").forEach((b) => { b.disabled = busy; });
}

setBusy(true);
(window.TriodyReady || Promise.resolve(null)).then(async (t) => {
  transport = t;
  renderState(await api("/api/state"));
  // The agent runs its own daily check without being asked, and may prepare a draft for approval.
  const brief = await api("/api/briefing", {});
  addMsg(brief.text, "bot");
  renderState(brief.state);
  await loadAudit();
  setBusy(false);
}).catch((err) => {
  const a = $("alert");
  a.className = "alert critical";
  a.textContent = "Could not start: " + (err && err.message ? err.message : String(err));
});
