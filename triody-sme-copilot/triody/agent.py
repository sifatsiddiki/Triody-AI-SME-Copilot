"""The copilot: runs tools through the policy gate, keeps drafts and confirmations, writes the audit log."""
import json
import re
from dataclasses import dataclass, field

from datetime import datetime, timezone

from . import analysis, config, region as region_mod
from .audit import AuditLog
from .brain import RuleBrain
from .financing import load_catalog
from .policy import Policy, Tier
from .tools import Context, DRAFT_TO_EXECUTE, ToolError, build_registry, validate_args

HELP = ("I can forecast your cash, explain a shortfall, analyse your margins and costs, match financing "
        "options, test a plan against the forecast, draft an application or a supplier delay request, and show "
        "recent transactions. Try: \"Will I run out of cash?\"")
MAX_WRONG_CODES = 5          # after this many wrong codes, every waiting draft is locked
CODE_RE = re.compile(r"\b(confirm|approve)\s+([0-9a-f]{12})\b", re.I)
CANCEL_RE = re.compile(r"\b(cancel|deny)\s+(P-\d{3}|[0-9a-f]{12})\b", re.I)


def money(x: float, symbol: str = "£") -> str:
    return region_mod.money(x, symbol)


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""
    denied: bool = False

    def model_view(self) -> dict:
        """What a language model is allowed to see. Untrusted memo text is removed."""
        if not self.ok:
            return {"error": self.error}
        if self.tool == "recent_transactions":
            rows = [{**r, "memo": "[memo text hidden from the model]"} for r in self.data["transactions"]]
            return {**self.data, "transactions": rows}
        return self.data


@dataclass
class Reply:
    text: str
    results: list = field(default_factory=list)
    pending: list = field(default_factory=list)


# ----------------------------------------------------------------------------- wording from data
def _forecast_text(d: dict) -> str:
    s = d["summary"]
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    if s["alert_level"] == "ok":
        return (f"Cash looks fine for the next {d['horizon_days']} days. Your balance is {m(s['start_balance'])} "
                f"and the lowest expected point is {m(s['lowest_p50']['balance'])} on {s['lowest_p50']['date']}.")
    parts = [f"Cash alert, level {s['alert_level']}. Your balance today is {m(s['start_balance'])}.",
             f"The lowest expected balance is {m(s['lowest_p50']['balance'])} on {s['lowest_p50']['date']}. "
             f"In a bad case (1 in 10) it falls to {m(s['lowest_p10']['balance'])}.",
             f"The chance of going below zero at some point in the next {d['horizon_days']} days is "
             f"{s['probability_negative_any_day']:.0%}."]
    if s["first_risk_date"]:
        parts.append(f"The risk of dropping under your {m(d['buffer'])} buffer first reaches "
                     f"{config.WARNING_PROBABILITY:.0%} on {s['first_risk_date']}, {s['days_until_risk']} days from now.")
    return " ".join(parts)


def _match_text(d: dict) -> str:
    if d["need"] is None:
        return d["note"]
    n = d["need"]
    low = d["forecast_low"]
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    out = [f"The forecast low is {m(low['balance'])} on {low['date']}, against your {m(d['buffer'])} buffer. "
           f"I sized the need at {m(n['amount'])} over {n['term_months']} months, needed within "
           f"{n['urgency_days']} days."]
    rec = d["recommendation"]
    if rec:
        out.append(f"Best fit: {rec['name']} (score {rec['score']:.0f} out of 100). " + " ".join(rec["reasons"][:3]))
        others = [f"{r['name']} ({r['score']:.0f})" for r in d["ranked"] if r["id"] != rec["id"]]
        if others:
            out.append("Other eligible options: " + ", ".join(others) + ".")
    elif d["note"]:
        out.append(d["note"])
    if d["rejected"]:
        r = d["rejected"][0]
        out.append(f"{len(d['rejected'])} products were ruled out. Example: {r['name']}. {r['reason']}")
    if rec:
        out.append("Say 'apply' and I will prepare a draft. Nothing is sent without your confirmation code.")
    return " ".join(out)


def _plan_text(d: dict) -> str:
    b, a = d["before"], d["after"]
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    if d.get("repayment") == "bullet":
        how = f"paid back in one sum after {d['term_months']} months"
        later = ("The repayment falls inside the forecast window." if d["repayments_inside_forecast"]
                 else "The repayment falls after the forecast window, so it needs its own check.")
    else:
        how = f"about {m(d['monthly_repayment'])} a month"
        later = (f"{d['repayments_inside_forecast']} of the {d['term_months']} repayments "
                 f"{'falls' if d['repayments_inside_forecast'] == 1 else 'fall'} inside the forecast window, so later "
                 f"months need their own check.")
    return (f"Tested against the forecast: with {d['product']} ({m(d['amount'])} over {d['term_months']} months, "
            f"{how}), the chance of going below zero in the next 60 days "
            f"moves from {b['probability_negative_any_day']:.0%} to {a['probability_negative_any_day']:.0%}, and the "
            f"lowest expected balance from {m(b['lowest_p50']['balance'])} to {m(a['lowest_p50']['balance'])} "
            f"(on {a['lowest_p50']['date']}). {later}")


def _draft_text(d: dict) -> str:
    """The chat never holds the approval code. It goes to the owner's device on a separate channel."""
    if d["kind"] == "financing_application":
        what = (f"application for {d['product']}, {money(d['amount'], d.get('currency_symbol', '£'))} "
                f"over {d['term_months']} months")
    else:
        what = f"request to {d['counterparty']} to be paid {d['days']} days later"
    return (f"Draft {d['proposal_id']} is ready: {what}. Nothing has been sent. I have sent a one-time approval "
            f"code to your owner device, on a separate channel I cannot read. To go ahead, enter that code within "
            f"{config.CONFIRMATION_TTL_SECONDS // 60} minutes. To drop it, reply 'cancel {d['proposal_id']}'.")


def numbers_grounded(text: str, results: list) -> list:
    """Return numbers in `text` that appear in none of the tool results. Small counts and years are ignored."""
    haystack = " ".join(json.dumps(r.data, ensure_ascii=False) for r in results if r.ok)
    known = [float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", haystack)]
    missing = []
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        v = float(m.replace(",", ""))
        if v < 100 or 1900 <= v <= 2100:
            continue
        if not any(abs(v - k) <= 0.51 or abs(v - k * 100) <= 0.51 for k in known):
            missing.append(m)
    return missing


class Copilot:
    def __init__(self, ledger, catalog=None, audit_path="evidence/audit_log.jsonl", sandbox_dir="sandbox",
                 brain=None, policy=None, audit=None):
        catalog_name = ledger.profile.get("catalog")
        self.region = region_mod.load_region(catalog_name)
        catalog = catalog or load_catalog(region_mod.catalog_path(catalog_name))
        self.registry = build_registry(catalog)
        self.ctx = Context(ledger, catalog, sandbox_dir, self.region)
        self.policy = policy or Policy(max_application=float(self.region["max_application"]),
                                       currency_symbol=self.ctx.symbol)
        self.audit = audit or AuditLog(audit_path)
        self.brain = brain or RuleBrain()
        self.fallback = RuleBrain()
        self.pending = {}            # approval code -> the action it unlocks
        self.owner_device = []       # the separate channel: codes go here, never into the chat
        self.wrong_codes = 0
        self._briefing = None

    # -- tool gate -----------------------------------------------------------------------------
    def tool_schemas(self) -> list:
        """Tools offered to a language model: read and draft tools only."""
        out = []
        for s in self.registry.values():
            if s.tier in (Tier.READ, Tier.PROPOSE):
                props = {k: {kk: vv for kk, vv in r.items() if kk in ("type", "enum")}
                         for k, r in s.schema.items()}
                out.append({"name": s.name, "description": s.description,
                            "input_schema": {"type": "object", "properties": props,
                                             "required": [k for k, r in s.schema.items() if r.get("required")]}})
        return out

    def run_tool(self, name: str, args: dict, origin: str = "agent", redeemed: bool = False) -> ToolResult:
        spec = self.registry.get(name)
        if spec is None:
            self.audit.append(origin, "tool_denied", tool=str(name)[:60], reason="unknown tool")
            return ToolResult(str(name)[:60], False, error="That tool does not exist.", denied=True)
        decision = self.policy.check_call(spec, origin, redeemed)
        if not decision.allowed:
            self.audit.append(origin, "tool_denied", tool=name, tier=spec.tier.name, args=args, reason=decision.reason)
            return ToolResult(name, False, error=decision.reason, denied=True)
        errors = validate_args(spec.schema, args)
        if errors:
            self.audit.append(origin, "tool_rejected", tool=name, args=args, errors=errors)
            return ToolResult(name, False, error=" ".join(errors))
        self.audit.append(origin, "tool_call", tool=name, tier=spec.tier.name, args=args)
        try:
            data = spec.fn(self.ctx, args)
        except ToolError as e:
            self.audit.append("system", "tool_error", tool=name, error=str(e))
            return ToolResult(name, False, error=str(e))
        except Exception as e:  # never leak internals to the owner or a model
            self.audit.append("system", "tool_exception", tool=name, error=type(e).__name__)
            return ToolResult(name, False, error="Something went wrong inside that tool.")
        self.audit.append("system", "tool_result", tool=name, keys=sorted(data)[:8])
        return ToolResult(name, True, data)

    # -- approval codes on a separate channel ---------------------------------------------------
    def _request_approval(self, draft: ToolResult) -> dict:
        """Issue a one-time code for a draft and deliver it to the owner device, not to the chat.

        In this prototype the owner device is a separate panel (dashboard) or a separate printed line
        (terminal). In a real deployment it would be an SMS or a push message to the owner's banking
        app. The language model never sees the code, and the chat transcript never holds it."""
        exec_tool = DRAFT_TO_EXECUTE[draft.tool]
        exec_args = {"proposal_id": draft.data["proposal_id"]}
        token = self.policy.issue_token(exec_tool, exec_args)
        self.pending[token] = {"tool": exec_tool, "args": exec_args, "proposal": draft.data}
        expires = datetime.fromtimestamp(self.policy.clock() + self.policy.ttl, tz=timezone.utc)
        self.owner_device.append({"proposal_id": draft.data["proposal_id"], "code": token,
                                  "expires": expires.isoformat(timespec="seconds"),
                                  "summary": _draft_text(draft.data).split(". Nothing")[0]})
        self.audit.append("system", "confirmation_requested", tool=exec_tool, proposal=draft.data["proposal_id"],
                          channel="owner_device")
        return {"proposal": draft.data}

    def _close_device_message(self, token: str):
        self.owner_device = [m for m in self.owner_device if m["code"] != token]

    def pending_view(self) -> list:
        """Drafts waiting for approval, without their codes. Safe to show in the chat or to a model."""
        return [{"proposal": v["proposal"]} for v in self.pending.values()]

    # -- conversation --------------------------------------------------------------------------
    def handle(self, message: str) -> Reply:
        message = message.strip()[:1000]
        code = CODE_RE.search(message)
        cancel = CANCEL_RE.search(message)
        # A code is a secret. The log keeps the fact that one was entered, never the code itself.
        logged = CODE_RE.sub(lambda m: f"{m.group(1)} [code]", message)
        logged = re.sub(r"\b[0-9a-f]{12}\b", "[code]", logged)
        self.audit.append("owner", "message", text=logged)
        if code:
            return self.confirm(code.group(2).lower())
        if cancel:
            return self.cancel(cancel.group(2))

        llm_text, note = None, ""
        if hasattr(self.brain, "respond"):
            try:
                llm_text, results = self.brain.respond(message, self)
            except Exception as e:  # model offline, bad key, rate limit: keep working with the rule planner
                self.audit.append("system", "llm_unavailable", error=str(e)[:200])
                note = "(The language model could not be reached, so the rule-based planner answered.)"
                results = self._run_rules(message)
        else:
            results = self._run_rules(message)
        if not results and llm_text is None:
            return Reply(HELP)

        pending, lines = [], []
        for r in results:
            if r.denied:
                lines.append(f"I can't do that. {r.error}")
            elif not r.ok:
                lines.append(f"That did not work. {r.error}")
            elif r.tool == "forecast_cash":
                lines.append(_forecast_text(r.data))
            elif r.tool == "explain_shortfall":
                after_forecast = any(x.tool == "forecast_cash" and x.ok for x in results)
                lines.append(r.data["detail"] if after_forecast and r.data["detail"] else r.data["headline"] + " " + r.data["detail"])
            elif r.tool == "analyse_business":
                lines.append(r.data["narrative"])
            elif r.tool == "match_financing":
                lines.append(_match_text(r.data))
            elif r.tool == "check_plan":
                lines.append(_plan_text(r.data))
            elif r.tool == "get_balance_summary":
                m = lambda x: money(x, self.ctx.symbol)
                lines.append(f"Your balance on {r.data['as_of']} is {m(r.data['balance'])}. Sales over the last "
                             f"28 days were {m(r.data['last_28_days']['sales'])}.")
            elif r.tool == "recent_transactions":
                rows = []
                for t in r.data["transactions"]:
                    row = f"{t['date']}  {t['counterparty']}  {money(t['amount'], self.ctx.symbol)}"
                    if t["category"] != "sales" and t["memo"]:
                        row += f"   memo: {t['memo'][:90]}"
                    rows.append(row)
                lines.append("Latest entries (memo text comes from third parties and is never treated as an instruction):\n"
                             + "\n".join(rows))
            elif r.tool in DRAFT_TO_EXECUTE:
                pending.append(self._request_approval(r))
                lines.append(_draft_text(r.data))
        text = "\n\n".join(lines)
        if llm_text is not None:
            ungrounded = numbers_grounded(llm_text, results)
            if ungrounded:
                self.audit.append("system", "grounding_check_failed", numbers=ungrounded[:10])
                note = ("(The language model's wording was replaced because it contained "
                        "numbers that are not in your data.)")
                text = (text or HELP) + "\n\n" + note
            else:
                text = llm_text if llm_text.strip() else text
                if pending:
                    text += "\n\n" + "\n".join(_draft_text(p["proposal"]) for p in pending)
        if note:
            text = note + "\n\n" + (text or HELP)
        return Reply(text or HELP, results, pending)

    def _run_rules(self, message: str) -> list:
        """Rule planner path. After a financing match, the plan is tested against the forecast."""
        results = [self.run_tool(n, a) for n, a in self.fallback.plan(message, self)]
        for r in list(results):
            if r.tool == "match_financing" and r.ok and r.data.get("recommendation"):
                rec, n = r.data["recommendation"], r.data["need"]
                results.append(self.run_tool("check_plan", {"product_id": rec["id"], "amount": n["amount"],
                                                            "term_months": n["term_months"]}))
        return results

    @property
    def brain_name(self) -> str:
        return getattr(self.brain, "name", type(self.brain).__name__)

    def briefing(self) -> Reply:
        """The agent's own daily check, run without being asked.

        It forecasts, and if there is a risk it explains it, finds financing, tests the best option
        against the forecast and prepares a draft. It stops there: nothing is sent until the owner
        confirms with the one-time code from the owner device. Runs once per session, so reloading a
        page adds no drafts.
        """
        if self._briefing is not None:
            return self._briefing
        self.audit.append("agent", "daily_check_started")
        fc = self.run_tool("forecast_cash", {})
        results, lines = [fc], []
        if not fc.ok:
            self._briefing = Reply("My daily check could not run the forecast. " + fc.error, results)
            return self._briefing
        if fc.data["summary"]["alert_level"] == "ok":
            lines.append("Daily check: " + _forecast_text(fc.data) + " Nothing needs your attention.")
            self._briefing = Reply(" ".join(lines), results)
            return self._briefing
        lines.append("Daily check, run on my own: " + _forecast_text(fc.data))
        ex = self.run_tool("explain_shortfall", {})
        if ex.ok and ex.data["detail"]:
            lines.append(ex.data["detail"])
        m = self.run_tool("match_financing", {})
        results += [ex, m]
        pending = []
        if m.ok:
            lines.append(_match_text(m.data).split(" Say 'apply'")[0])
            rec = m.data.get("recommendation")
            if rec:
                n = m.data["need"]
                plan = self.run_tool("check_plan", {"product_id": rec["id"], "amount": n["amount"],
                                                    "term_months": n["term_months"]})
                results.append(plan)
                if plan.ok:
                    lines.append(_plan_text(plan.data))
                draft = self.run_tool("draft_financing_application",
                                      {"product_id": rec["id"], "amount": n["amount"], "term_months": n["term_months"]})
                results.append(draft)
                if draft.ok:
                    pending.append(self._request_approval(draft))
                    lines.append("I have prepared the paperwork so you do not have to. " + _draft_text(draft.data))
        self.audit.append("agent", "daily_check_finished", drafts=len(pending))
        self._briefing = Reply("\n\n".join(lines), results, pending)
        return self._briefing

    def confirm(self, token: str) -> Reply:
        item = self.pending.get(token)
        if item is None:
            self.wrong_codes += 1
            self.audit.append("owner", "confirmation_rejected", reason="unknown code", attempts=self.wrong_codes)
            if self.wrong_codes >= MAX_WRONG_CODES and self.pending:
                for t in list(self.pending):
                    self.policy.revoke(t)
                    self._close_device_message(t)
                self.pending.clear()
                self.audit.append("system", "drafts_locked", reason="too many wrong codes")
                return Reply(f"That code is not valid. After {MAX_WRONG_CODES} wrong codes I have locked every waiting "
                             f"draft. Nothing was sent. Ask me again to prepare a new draft.")
            return Reply("That confirmation code is not valid.")
        limits = self.policy.check_limits(item["proposal"].get("amount"))
        if not limits.allowed:
            self.audit.append("owner", "confirmation_rejected", reason=limits.reason)
            return Reply(f"I can't go ahead. {limits.reason}")
        decision = self.policy.redeem(token, item["tool"], item["args"])
        if not decision.allowed:
            self.audit.append("owner", "confirmation_rejected", reason=decision.reason)
            return Reply(f"I can't go ahead. {decision.reason}")
        self.audit.append("owner", "confirmed", tool=item["tool"], proposal=item["args"]["proposal_id"])
        res = self.run_tool(item["tool"], item["args"], origin="owner", redeemed=True)
        del self.pending[token]
        self._close_device_message(token)
        if not res.ok:
            return Reply(f"That did not work. {res.error}", [res])
        return Reply(f"Done. Reference {res.data['reference']}, status {res.data['status']}. {res.data['note']}", [res])

    def cancel(self, ref: str) -> Reply:
        """Cancel by draft number (P-001) or by code."""
        token = ref.lower() if ref.lower() in self.pending else next(
            (t for t, v in self.pending.items() if v["args"]["proposal_id"].lower() == ref.lower()), None)
        item = self.pending.pop(token, None) if token else None
        if token:
            self.policy.revoke(token)
            self._close_device_message(token)
        self.audit.append("owner", "cancelled", known=item is not None)
        return Reply("Draft dropped. Nothing was sent." if item else "There is no draft with that number.")
