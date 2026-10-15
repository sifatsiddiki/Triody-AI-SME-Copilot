"""The copilot: runs tools through the policy gate, keeps drafts and confirmations, writes the audit log."""
import json
import re
from dataclasses import dataclass, field

from datetime import datetime, timezone

from datetime import date as _date

from . import analysis, config, region as region_mod
from .audit import AuditLog
from .intent import fmt_day
from .brain import RuleBrain
from .financing import load_catalog
from .policy import Policy, Tier
from .tools import Context, DRAFT_TO_EXECUTE, ToolError, build_registry, validate_args

HELP = ("I can answer questions about your own books and your cash:\n"
        "- figures for any period: \"sales today\", \"costs last month\", \"profit in September\", "
        "\"wages this year\"\n"
        "- the cash forecast: \"will I run out of cash?\", \"why is cash tight?\"\n"
        "- margins, cost trends and break-even: \"how are my margins?\"\n"
        "- financing that fits, tested against the forecast: \"what financing fits?\", then \"apply\"\n"
        "- a request to a supplier to pay later, and your recent transactions\n"
        "Nothing is sent and no money moves without the code from your owner device.")
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
    suggestions: list = field(default_factory=list)


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
    if s.get("first_negative_date_p50"):
        parts.append(f"On the expected path, cash runs out around {s['first_negative_date_p50']}.")
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
           f"I sized the gap at {m(n['amount'])}, needed within {n['urgency_days']} days."]
    rec = d["recommendation"]
    if rec:
        term = rec.get("term_months", n["term_months"])
        out.append(f"Best fit: {rec['name']} over {term} months (score {rec['score']:.0f} out of 100). "
                   + " ".join(rec["reasons"][:4]))
        others = [f"{r['name']} ({r['score']:.0f})" for r in d["ranked"] if r["id"] != rec["id"]]
        if others:
            out.append("Other eligible options: " + ", ".join(others) + ".")
        if d["note"]:
            out.append(d["note"])
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
    if d.get("tax_deferral"):
        how = (f"paying {m(d['amount'])} of the tax bill later in {d['term_months']} instalments of about "
               f"{m(d['monthly_repayment'])}. No money is paid to you")
    elif d.get("repayment") == "bullet":
        how = f"{m(d['amount'])}, paid back in one sum after {d['term_months']} months"
    else:
        how = f"{m(d['amount'])} over {d['term_months']} months, about {m(d['monthly_repayment'])} a month"
    out = [f"Tested against the forecast: {d['product']}, {how}."]
    share = d.get("repayment_share_of_free_cash_flow")
    if share is not None:
        out.append(f"Each payment is {share:.0%} of your average monthly free cash flow of "
                   f"{m(d['monthly_free_cash_flow'])} (the limit I use is {d['max_repayment_share']:.0%}).")
    out.append(f"Over the next {d['near_term_days']} days, the chance of going below zero moves from "
               f"{b['probability_negative_any_day']:.0%} to {a['probability_negative_any_day']:.0%}, and the lowest "
               f"expected balance from {m(b['lowest_p50']['balance'])} to {m(a['lowest_p50']['balance'])} "
               f"(on {a['lowest_p50']['date']}).")
    f = d.get("full_term")
    if f:
        fb, fa = f["before"], f["after"]
        n, inside = d["repayments"], d["repayments_inside_full_test"]
        count = (f"all {n} payments" if inside == n else f"{inside} of the {n} payments")
        out.append(f"Over the whole plan, to {f['end']} ({count} counted): the chance of going below zero moves "
                   f"from {fb['probability_negative_any_day']:.0%} to {fa['probability_negative_any_day']:.0%}, and the "
                   f"lowest expected balance from {m(fb['lowest_p50']['balance'])} to {m(fa['lowest_p50']['balance'])}.")
        if fa["lowest_p50"]["balance"] < 0:
            out.append(f"Even with the plan, the longer view dips below zero (lowest expected "
                       f"{m(fa['lowest_p50']['balance'])} around {fa['lowest_p50']['date']}), so it buys time but "
                       f"does not close the gap on its own. The cost findings under margins and costs matter too.")
        if inside < n:
            out.append(f"The last {n - inside} payments fall after the {f['days']}-day test and rest on the free "
                       f"cash flow check above.")
        out.append("Ranges this far ahead are wide, so treat the whole-plan figures as a stress test, "
                   "not a prediction.")
    return " ".join(out)


def _draft_text(d: dict) -> str:
    """The chat never holds the approval code. It goes to the owner's device on a separate channel."""
    sym = d.get("currency_symbol", "£")
    if d["kind"] == "financing_application" and d.get("tax_deferral"):
        what = (f"request to {d.get('tax_authority', 'HMRC')} to pay {money(d['amount'], sym)} of your tax bill "
                f"over {d['term_months']} months, about {money(d.get('monthly_repayment', 0), sym)} a month")
    elif d["kind"] == "financing_application":
        what = (f"application for {d['product']}, {money(d['amount'], sym)} "
                f"over {d['term_months']} months")
    else:
        what = f"request to {d['counterparty']} to be paid {d['days']} days later"
    return (f"Draft {d['proposal_id']} is ready: {what}. Nothing has been sent. I have sent a one-time approval "
            f"code to your owner device, on a separate channel I cannot read. To go ahead, enter that code within "
            f"{config.CONFIRMATION_TTL_SECONDS // 60} minutes. To drop it, reply 'cancel {d['proposal_id']}'.")


# ----------------------------------------------------------------------------- figures for a period
METRIC_NAMES = {"top_payees": "Biggest costs", "top_suppliers": "Biggest suppliers", "payee": "Payments",
                "trading_days": "Trading days", "sales": "Sales", "costs": "Costs", "supplier": "Supplier bills", "payroll": "Wages",
                "rent": "Rent", "utilities": "Utilities", "packaging": "Packaging", "fees": "Card and bank fees",
                "tax": "Tax paid", "loan": "Loan repayments", "profit": "Operating profit",
                "net_cash": "Net cash flow", "transactions": "Transactions", "average": "Average daily sales",
                "best_day": "Best day", "worst_day": "Slowest trading day", "summary": "Summary"}


def _when(label: str) -> str:
    """Turn a period label into words that follow a noun: 'Sales in October 2026', 'Sales today'."""
    if label[:1].isdigit() and " to " in label:
        return "from " + label
    if re.fullmatch(r"([A-Z][a-z]+day )?\d{1,2} [A-Z][a-z]{2} \d{4}", label):
        return "on " + label
    if label.startswith("the "):
        return "over " + label
    if label.startswith("all "):
        return "across " + label
    if label[:1].isupper() or label[:1].isdigit():
        return "in " + label
    return label


def _day_words(iso: str) -> str:
    d = _date.fromisoformat(iso)
    return f"{d.strftime('%A')} {fmt_day(d)}"


def _metric_value(metric: str, r: dict, m) -> str:
    if metric == "average":
        return f"{m(r['average_daily_sales'])} a day, {m(r['average_daily_sales'] * 7)} a week"
    if metric == "trading_days":
        return f"{r['trading_days']} of {r['days']} days"
    if metric in ("best_day", "worst_day"):
        day = r[metric]
        return f"{_day_words(day['date'])}, {m(day['sales'])}" if day else "no trading days"
    simple = {"sales": "sales", "costs": "total_costs", "profit": "operating_profit", "net_cash": "net_cash"}
    if metric in simple:
        return m(r[simple[metric]])
    if metric == "transactions":
        return str(r["transactions"])
    if metric == "summary":
        return f"sales {m(r['sales'])}, costs {m(r['total_costs'])}, net {m(r['net_cash'])}"
    return m(r["costs"].get(metric, 0.0))


def _change(new: float, old: float, m, days: int = None, old_days: int = None) -> str:
    """Change from old to new. Periods of different lengths are compared per day, so 14 days of October
    are not set against all 30 days of September."""
    if days and old_days and days != old_days:
        a, b = new / days, old / old_days
        if not b:
            return f"{m(new)} against {m(old)}"
        pct = 100 * (a - b) / abs(b)
        word = "level" if abs(pct) < 0.5 else f"{'up' if pct > 0 else 'down'} {abs(pct):.0f}%"
        return (f"{m(old)} over {old_days} days. Per day that is {m(a)} against {m(b)}, "
                f"{word}" + ("" if abs(pct) < 0.5 else " per day"))
    if not old:
        return f"against {m(old)}"
    pct = 100 * (new - old) / abs(old)
    if abs(pct) < 0.5:
        return f"level with {m(old)}"
    return f"{'up' if pct > 0 else 'down'} {abs(pct):.0f}% on {m(old)}"


def _report_text(d: dict, as_of: str) -> str:
    metric = d.get("metric", "summary")
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    name = METRIC_NAMES.get(metric, metric)
    if "overview" in d:
        lines = [f"{name} at a glance (your books run to {fmt_day(_date.fromisoformat(d['as_of']))}):"]
        for r in d["overview"]:
            value = _metric_value(metric, r, m) if r["available"] else "nothing recorded"
            extra = " (partial: your books start later)" if r.get("partial") else ""
            lines.append(f"- {r['label']}: {value}{extra}")
        avg = next((r for r in d["overview"] if r["label"].startswith("Average full month")), None)
        if avg and metric in avg.get("costs", {}) and avg["costs"].get(metric):
            lines.append(f"At that monthly average, about {m(avg['costs'][metric] * 12)} a year.")
        lines.append("Ask for any other period, for example \"" + name.lower() + " in September\" or \""
                     + name.lower() + " last 30 days\".")
        return "\n".join(lines)
    label = d["label"]
    if not d["available"]:
        return f"I can't give {name.lower()} {_when(label)}. {d['reason']}"
    when = _when(label)
    out = []
    c = d.get("compare")
    if metric == "sales":
        out.append(f"Sales {when}: {m(d['sales'])}.")
        if d["days"] > 1:
            out.append(f"That is {m(d['average_daily_sales'])} a day over {d['days']} days, "
                       f"with {d['trading_days']} trading days.")
        if c:
            out.append(f"Compared with {c['label']}: {_change(d['sales'], c['sales'], m, d['days'], c['days'])}.")
    elif metric == "costs":
        out.append(f"Costs {when}: {m(d['total_costs'])} in total.")
        big = sorted(((k, v) for k, v in d["costs"].items() if v > 0), key=lambda kv: -kv[1])[:3]
        if big:
            out.append("Biggest: " + ", ".join(f"{analysis.COST_LABELS.get(k, k)} {m(v)}" for k, v in big) + ".")
        if c:
            out.append(f"Compared with {c['label']}: "
                       f"{_change(d['total_costs'], c['total_costs'], m, d['days'], c['days'])}.")
    elif metric == "profit":
        var = d["sales"] - d["gross_profit"]
        fixed = d["gross_profit"] - d["operating_profit"]
        margin = f" ({d['gross_margin_pct']:.0f}% margin)" if d["gross_margin_pct"] is not None else ""
        out.append(f"Profit {when}: sales {m(d['sales'])}, less supplier and packaging bills {m(var)}, gives "
                   f"gross profit of {m(d['gross_profit'])}{margin}. After wages, rent, utilities, loan repayments "
                   f"and fees ({m(fixed)}), operating profit is {m(d['operating_profit'])}.")
        if d["costs"].get("tax"):
            out.append(f"Tax paid in the period is shown apart: {m(d['costs']['tax'])}.")
        if d["days"] < 28:
            out.append("Rent and other monthly bills land on single days, so short periods swing a lot.")
    elif metric == "net_cash":
        out.append(f"Cash flow {when}: {m(d['money_in'])} in, {m(d['money_out'])} out, net {m(d['net_cash'])}.")
    elif metric == "transactions":
        out.append(f"{d['transactions']} transactions {when}: sales takings on {d['trading_days']} days and "
                   f"{d['payments_out']} payments out.")
    elif metric == "average":
        out.append(f"Average sales {when}: {m(d['average_daily_sales'])} a day across {d['days']} days, or "
                   f"{m(d['average_sales_per_trading_day'])} per trading day. That is about "
                   f"{m(d['average_daily_sales'] * 7)} a week.")
    elif metric == "trading_days":
        out.append(f"You traded on {d['trading_days']} of the {d['days']} days {when}.")
        if d["trading_days"] < d["days"]:
            out.append(f"No takings were recorded on the other {d['days'] - d['trading_days']}.")
    elif metric == "payee":
        cp = d.get("counterparty") or {}
        if not cp.get("payments") and not cp.get("received"):
            out.append(f"Nothing went to or came from {cp.get('name', 'them')} {when}.")
        else:
            out.append(f"You paid {cp['name']} {m(cp['paid'])} {when}, in {cp['payments']} payment"
                       f"{'s' if cp['payments'] != 1 else ''}" + (f", the last on {fmt_day(_date.fromisoformat(cp['last']))}."
                                                                  if cp.get("last") else "."))
            if d["total_costs"]:
                out.append(f"That is {100 * cp['paid'] / d['total_costs']:.0f}% of everything paid out in the period.")
    elif metric in ("best_day", "worst_day"):
        day = d[metric]
        if day is None:
            out.append(f"There were no trading days {when}.")
        else:
            out.append(f"{name} {when}: {_day_words(day['date'])}, with sales of {m(day['sales'])}.")
    elif metric == "summary":
        out.append(f"{when[0].upper() + when[1:]}: sales {m(d['sales'])}, costs {m(d['total_costs'])}, "
                   f"net cash {m(d['net_cash'])}, operating profit {m(d['operating_profit'])}.")
        if d["costs"].get("tax"):
            out.append(f"Costs include {m(d['costs']['tax'])} of tax, which is why net cash and operating profit differ.")
        if c:
            out.append(f"{c['label'][0].upper() + c['label'][1:]}: sales {m(c['sales'])}, costs "
                       f"{m(c['total_costs'])}, net cash {m(c['net_cash'])}, operating profit "
                       f"{m(c['operating_profit'])}.")
            def pct(a, b):
                if not b:
                    return "no change to compare"
                v = 100 * (a - b) / abs(b)
                return "level" if abs(v) < 0.5 else f"{'up' if v > 0 else 'down'} {abs(v):.0f}%"
            if d["days"] != c["days"]:
                sa, sb = d["average_daily_sales"], c["average_daily_sales"]
                ca, cb = d["average_daily_costs"], c["average_daily_costs"]
                out.append(f"The periods differ in length ({d['days']} and {c['days']} days), so per day: sales "
                           f"{m(sa)} against {m(sb)} ({pct(sa, sb)}), costs {m(ca)} against {m(cb)} ({pct(ca, cb)}).")
            else:
                out.append(f"Sales {pct(d['sales'], c['sales'])}, costs {pct(d['total_costs'], c['total_costs'])}.")
            if d["days"] < 28 or c["days"] < 28:
                out.append("Rent and wages land on single days, so profit over part of a month swings a lot.")
            out += c.get("notes", [])
    elif metric == "top_suppliers":
        rows = [r for r in d.get("payees", []) if r["category"] in ("supplier", "packaging")]
        if not rows:
            out.append(f"No supplier bills were paid {when}.")
        else:
            total = d.get("variable_costs") or sum(r["amount"] for r in rows)
            out.append(f"Your biggest supplier {when} was {rows[0]['counterparty']}: {m(rows[0]['amount'])}, "
                       f"{100 * rows[0]['amount'] / total:.0f}% of supplier and packaging bills ({m(total)}) and "
                       f"{rows[0]['share_pct']:.0f}% of everything paid out.")
            if len(rows) > 1:
                out.append("Then: " + "; ".join(f"{r['counterparty']} {m(r['amount'])}" for r in rows[1:4]) + ".")
            out.append(f"Ask \"delay {rows[0]['counterparty']} by 7 days\" to draft a request for more time to pay.")
    elif metric == "top_payees":
        rows = d.get("payees", [])
        if not rows:
            out.append(f"Nothing was paid out {when}.")
        else:
            top = rows[0]
            out.append(f"Your biggest cost {when} was {top['counterparty']} "
                       f"({analysis.COST_LABELS.get(top['category'], top['category'])}): {m(top['amount'])}, "
                       f"{top['share_pct']:.0f}% of everything paid out ({m(d['total_costs'])}).")
            out.append("Next: " + "; ".join(f"{r['counterparty']} {m(r['amount'])} ({r['share_pct']:.0f}%)"
                                            for r in rows[1:5]) + ".")
            sup = [r for r in rows if r["category"] == "supplier"]
            if sup and sup[0] is not top:
                out.append(f"Your biggest supplier is {sup[0]['counterparty']} at {m(sup[0]['amount'])}.")
    else:
        value = d["costs"].get(metric, 0.0)
        if value:
            out.append(f"{name} {when}: {m(value)}.")
        else:
            out.append(f"{name} {when}: nothing was paid.")
        if c:
            out.append(f"Compared with {c['label']}: "
                       f"{_change(value, c['costs'].get(metric, 0.0), m, d['days'], c['days'])}.")
    out += d.get("notes", [])
    if d["start"] == d["end"] == as_of:
        out.append("Today here means the latest day in your books.")
    return " ".join(out)


def _month_words(ym: str) -> str:
    try:
        return _date.fromisoformat(ym + "-01").strftime("%B %Y")
    except ValueError:
        return ym


def _friendly(text: str) -> str:
    """Write ISO months and dates the way people read them: 2026-09 -> September 2026."""
    text = re.sub(r"\b(20\d\d-\d\d-\d\d)\b", lambda mo: fmt_day(_date.fromisoformat(mo.group(1))), text)
    return re.sub(r"\b(20\d\d-\d\d)\b", lambda mo: _month_words(mo.group(1)), text)


def _bills_text(d: dict) -> str:
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    items = d["items"]
    if d.get("focus"):
        hits = d.get("matches") or []
        if not hits:
            return (f"I can't find a {d['focus']} payment coming up in your books or the bills you have told me "
                    f"about. Ask \"what bills are coming up?\" for the full list.")
        out = []
        for k, h in enumerate(hits[:2]):
            when = fmt_day(_date.fromisoformat(h["date"]))
            about = "about " if h.get("estimate") else ""
            line = (f"{'Your next' if k == 0 else 'After that, the next'} {d['focus']} payment: {h['label']}, "
                    f"{about}{m(h['amount'])} due on {when}.")
            if k == 0 and "expected_balance_after" in h:
                bal, risk = h["expected_balance_after"], h.get("chance_below_zero") or 0
                line += f" With sales coming in, your expected balance that day after paying it is {m(bal)}."
                if bal < 0:
                    line += " On the expected path there is not enough cash to cover it."
                elif risk >= 0.1:
                    line += (f" It is covered on the expected path, but there is a {risk:.0%} chance of being below "
                             f"zero that day, so keep spending tight until then.")
                else:
                    line += " That covers it."
            out.append(line)
        return " ".join(out)
    if not items:
        return f"No bills are due in the next {d['days']} days that I know of."
    tax = [i for i in items if i["tax"]]
    out = []
    if tax:
        t = tax[0]
        out.append(f"Your next tax bill: {t['label']}, {m(t['amount'])} due on "
                   f"{fmt_day(_date.fromisoformat(t['date']))}.")
    elif d.get("later_tax"):
        t = d["later_tax"][0]
        out.append(f"No tax is due in the next {d['days']} days. The next one I know of is {t['label']}, "
                   f"{m(t['amount'])} on {fmt_day(_date.fromisoformat(t['date']))}.")
    out.append(f"Bills due by {fmt_day(_date.fromisoformat(d['end']))} add up to about {m(d['total'])}:")
    big = sorted(items, key=lambda i: -i["amount"])[:8]
    for i in sorted(big, key=lambda i: i["date"]):
        about = "about " if i.get("estimate") else ""
        out.append(f"- {fmt_day(_date.fromisoformat(i['date']))}: {i['label']}, {about}{m(i['amount'])}")
    if len(items) > len(big):
        out.append(f"- plus {len(items) - len(big)} smaller payments")
    out.append(f"Your balance is {m(d['balance'])}. With sales coming in, the expected low is "
               f"{m(d['lowest']['balance'])} on {d['lowest']['date']} (buffer {m(d['buffer'])}).")
    return "\n".join(out)


def _health_text(d: dict) -> str:
    word = {"good": "Good", "watch": "Watch", "problem": "Problem"}
    out = [f"Overall: {d['verdict']}. {d['problems']} problem{'s' if d['problems'] != 1 else ''} and "
           f"{d['watch']} thing{'s' if d['watch'] != 1 else ''} to watch."]
    order = {"problem": 0, "watch": 1, "good": 2}
    for c in sorted(d["checks"], key=lambda c: order[c["status"]]):
        out.append(f"- {word[c['status']]}: {c['area']}. {c['detail']}")
    out.append("Ask \"how can I improve cash flow?\" for actions, or \"why is cash tight?\" for the cause.")
    return _friendly("\n".join(out))


def _hire_text(d: dict) -> str:
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    p = d.get("payroll")
    out = []
    if p:
        out.append(f"Your wages were {m(p['wages'])} in {p['month']}, {p['share']:.0f}% of sales, and operating "
                   f"profit was {m(p['operating_profit'])}.")
    fcf = d["free_cash_flow"]
    if fcf <= 0:
        out.append(f"On average nothing is left after every payment ({m(fcf)} a month), so a new hire would have to "
                   f"bring in more sales than they cost from the first month.")
    else:
        out.append(f"On average {m(fcf)} a month is left after every payment. A hire that costs more than that, "
                   f"including employer costs, would need to bring in extra sales to pay for itself.")
    cash = next((c for c in d["checks"] if c["area"].startswith("Cash")), None)
    if cash and cash["status"] != "good":
        out.append(f"Cash is tight right now: {cash['detail']} Waiting until after that point is safer.")
    out.append("Tell me the monthly cost, for example \"can I afford £1,800 a month for a new barista\", and I "
               "will test it against the forecast.")
    return _friendly(" ".join(out))


def _advice_text(d: dict) -> str:
    if not d["actions"]:
        return "Nothing in your books needs action right now. Cash, costs and sales look in line."
    out = ["What I would look at first, biggest effect first:"]
    for k, a in enumerate(d["actions"], 1):
        out.append(f"{k}. {a['title']}. {a['detail']}")
    out.append("These are suggestions from your own numbers, not regulated advice.")
    return _friendly("\n".join(out))


def _what_if_text(d: dict) -> str:
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    b, a = d["before"], d["after"]
    if d["sales_pct"]:
        what = (f"If {d['label'] or 'sales'} {'rise' if d['sales_pct'] > 0 else 'fall'} by {abs(d['sales_pct']):g}% "
                f"(about {m(abs(d['sales_change_per_month']))} a month of sales)")
        if d["label"] == "prices":
            what += ", with the same number of customers"
    elif d["monthly"]:
        kind = "saving" if d["monthly"] < 0 else "cost"
        what = f"With a monthly {kind} of {m(abs(d['monthly']))}" + (f" for the {d['label']}" if d["label"] not in ("", "this", "a cost saving") else "")
    else:
        kind = "saving" if d["amount"] < 0 else "spend"
        what = f"With a one-off {kind} of {m(abs(d['amount']))}" + (f" on the {d['label']}" if d["label"] not in ("", "this", "a cost saving") else "")
        what += f" on {fmt_day(_date.fromisoformat(d['start']))}"
    improving = (d["amount"] or 0) < 0 or (d["monthly"] or 0) < 0 or (d["sales_pct"] or 0) > 0
    if improving:
        low = a["lowest_p50"]["balance"]
        verdict = ("That would close the gap: the expected low stays above your buffer." if low >= d["buffer"] else
                   "That helps: cash stays above zero on the expected path, though under your buffer." if low >= 0
                   else "That helps, but not enough on its own.")
    elif (d["sales_pct"] or 0) < 0:
        verdict = {"affordable": "You would cope with that.", "tight": "That would make cash tight.",
                   "not now": "That would put you in trouble."}[d["verdict"]]
    else:
        verdict = {"affordable": "Yes, that looks affordable.",
                   "tight": "Possible, but it would be tight.",
                   "not now": "Not now. Your cash cannot take it safely."}[d["verdict"]]
    out = [verdict,
           f"{what}, the lowest expected balance over the next {d['days']} days goes from "
           f"{m(b['lowest_p50']['balance'])} to {m(a['lowest_p50']['balance'])} (around {a['lowest_p50']['date']}), "
           f"and the chance of going below zero from {b['probability_negative_any_day']:.0%} to "
           f"{a['probability_negative_any_day']:.0%}."]
    if d["monthly"] and d["monthly"] > 0:
        share = d.get("monthly_share_of_free_cash_flow")
        if share is None:
            out.append(f"Your average free cash flow is {m(d['free_cash_flow'])} a month, so there is nothing spare "
                       f"to pay it from.")
        else:
            out.append(f"It would take {share:.0%} of your average free cash flow of {m(d['free_cash_flow'])} a month.")
    if d.get("headroom") is not None and d["verdict"] != "affordable":
        if d["headroom"] > 0:
            out.append(f"On the expected path you could spend up to about {m(d['headroom'])} now and stay above your "
                       f"{m(d['buffer'])} buffer.")
        else:
            out.append(f"The expected path already falls under your {m(d['buffer'])} buffer, so any extra spend "
                       f"before {b['lowest_p50']['date']} adds risk. Waiting until after then is safer.")
    out.append("Nothing has changed. This is a test on the forecast." if improving or d["sales_pct"]
               else "Nothing has been spent. This is a test on the forecast.")
    return _friendly(" ".join(out))


def _weeks_text(d: dict) -> str:
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    if not d["available"]:
        return d["reason"]
    span = lambda r: f"week of {fmt_day(_date.fromisoformat(r['start']))}"
    rows = d["rows"]
    out = [f"Over the last {len(rows)} full weeks, sales averaged {m(d['average_sales'])} a week. Best: {span(d['best'])}, "
           f"{m(d['best']['sales'])}. Worst: {span(d['worst'])}, {m(d['worst']['sales'])}."]
    out.append("Week by week: " + "; ".join(f"{fmt_day(_date.fromisoformat(r['start']))} {m(r['sales'])}" for r in rows[-8:])
               + ".")
    return " ".join(out)


def _late_tax_text(region: str, authority: str) -> str:
    if region == "CN":
        return (f"Paying tax late in China brings a daily late fee on the unpaid amount. A deferral needs approval "
                f"from the {authority} before the due date. Ask \"what financing fits?\" to see the deferral tested.")
    return ("If VAT is paid late, HMRC charges late payment interest from the due date (base rate plus 4%, 7.75% a "
            "year when the catalogue was checked) and a penalty from day 15 (3% of the VAT then owed), with more "
            "after day 30. Asking HMRC for Time to Pay by day 15 avoids the penalty, though interest still runs. "
            "Ask \"what financing fits?\" to see a payment plan tested against your forecast. Check the current rates "
            "on gov.uk before you decide.")


def _spend_check_text(fc: dict) -> str:
    s = fc["summary"]
    m = lambda x: money(x, fc.get("currency_symbol", "£"))
    head = max(0.0, s["lowest_p50"]["balance"] - fc["buffer"])
    if s["alert_level"] == "ok" and head > 0:
        start = (f"Cash looks fine for the next {fc['horizon_days']} days. On the expected path you could spend up to "
                 f"about {m(head)} and stay above your {m(fc['buffer'])} buffer.")
    else:
        start = (f"Not a great time for a big purchase. Cash is expected to fall to {m(s['lowest_p50']['balance'])} "
                 f"around {s['lowest_p50']['date']}, under your {m(fc['buffer'])} buffer, so anything you buy before then "
                 f"adds risk.")
    return (start + " Tell me the price, for example \"can I afford £2,000 for a new espresso machine\", and I will "
                    "test it against the forecast.")


def _weekday_text(d: dict) -> str:
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    if not d["available"]:
        return "I can't give sales by day for that period. " + d["reason"]
    span = f"{fmt_day(_date.fromisoformat(d['start']))} to {fmt_day(_date.fromisoformat(d['end']))}"
    rows = [r for r in d["rows"] if r["days"]]
    wd = d.get("weekday")
    out = []
    if wd is not None:
        r = d["rows"][wd]
        if not r["trading_days"]:
            out.append(f"You did not trade on {r['name']}s from {span}.")
        elif d.get("closing") and d.get("variable_cost_ratio_pct") is not None:
            keep = r["average"] * (1 - d["variable_cost_ratio_pct"] / 100)
            out.append(f"{r['name']}s average {m(r['average'])} of sales. After supplier and packaging costs "
                       f"({d['variable_cost_ratio_pct']:.0f}% of sales) that leaves about {m(keep)} a day towards wages, "
                       f"rent and other costs. Closing makes sense only if the costs you would save that day (staff hours, "
                       f"energy) are more than about {m(keep)}, and some customers may come on other days instead.")
            return " ".join(out)
        else:
            diff = 100 * (r["average"] - d["average_trading_day"]) / d["average_trading_day"]
            out.append(f"{r['name']}s from {span}: {m(r['sales'])} over {r['trading_days']} days, {m(r['average'])} "
                       f"on average, {abs(diff):.0f}% {'above' if diff >= 0 else 'below'} a typical trading day "
                       f"({m(d['average_trading_day'])}).")
    else:
        out.append(f"Average sales by day of the week, {span}:")
        for r in sorted(rows, key=lambda r: -r["average"]):
            out.append(f"- {r['name']}: {m(r['average'])}" + ("" if r["trading_days"] else " (closed)"))
        out.append(f"Best: {d['best']}. Quietest: {d['worst']}.")
    out += d.get("notes", [])
    return "\n".join(out) if wd is None else " ".join(out)


def _months_text(a: dict, metric: str = None) -> str:
    m = lambda x: money(x, a.get("currency_symbol", "£"))
    rows = a["months"]
    full = [r for r in rows if r["full"]]
    out = []
    if full and metric in ("costs", "supplier", "payroll", "rent", "utilities", "packaging", "fees", "loan"):
        total = lambda r: r["variable_costs"] + r["fixed_costs"]
        avg = sum(total(r) for r in full) / len(full)
        hi = max(full, key=total)
        out.append(f"Your costs (before tax) average {m(avg)} a full month. Highest: {_month_words(hi['month'])} "
                   f"({m(total(hi))}).")
    elif full:
        key = "operating_profit" if metric == "profit" else "sales"
        best = max(full, key=lambda r: r[key])
        worst = min(full, key=lambda r: r[key])
        word = "profit" if key == "operating_profit" else "sales"
        out.append(f"Best full month for {word}: {_month_words(best['month'])} ({m(best[key])}). Weakest: "
                   f"{_month_words(worst['month'])} ({m(worst[key])}).")
        losses = [r for r in full if r["operating_profit"] < 0]
        out.append("Every full month made an operating profit." if not losses else
                   f"Operating loss in {', '.join(_month_words(r['month']) for r in losses)}.")
        if len(full) >= 3:
            last3 = full[-3:]
            trend = last3[-1]["sales"] - last3[0]["sales"]
            out.append(f"Over the last three full months, sales went {'up' if trend > 0 else 'down'} from "
                       f"{m(last3[0]['sales'])} to {m(last3[-1]['sales'])}.")
    out.append("Month by month (costs before tax):")
    for r in rows:
        tag = "" if r["full"] else " (part month)"
        costs = r["variable_costs"] + r["fixed_costs"]
        out.append(f"- {_month_words(r['month'])}{tag}: sales {m(r['sales'])}, costs {m(costs)}, operating profit "
                   f"{m(r['operating_profit'])}")
    out.append("Rent, wages and tax land on single days, so part months are not comparable.")
    return "\n".join(out)


def _balance_on_text(d: dict) -> str:
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    when = fmt_day(_date.fromisoformat(d["date"]))
    if not d["available"]:
        return f"I can't give a balance for {when}. {d['reason']}"
    return (f"Expected balance on {when}: about {m(d['p50'])}. Eight times in ten it lands between {m(d['p10'])} "
            f"and {m(d['p90'])}. The chance it is below zero that day is {d['prob_below_zero']:.0%}. Your safety "
            f"buffer is {m(d['buffer'])}.")


def _unusual_text(d: dict) -> str:
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    if not d["found"]:
        return (f"Nothing unusual in the last {d['days']} days: {d['checked']} entries checked for oversized payments, "
                f"new payees and memos that read like instructions.")
    out = [f"I checked {d['checked']} entries from the last {d['days']} days. Worth a look:"]
    for f in d["found"]:
        out.append(f"- {fmt_day(_date.fromisoformat(f['date']))}, {f['counterparty']}, {m(f['amount'])}: {f['why']}")
    out.append("If a supplier's bank details or payment request look odd, call them on a number you already have "
               "before paying.")
    return "\n".join(out)


def _why_text(d: dict) -> str:
    """Why a figure moved between two periods: the biggest changes, line by line."""
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    metric = d.get("metric", "profit")
    c = d.get("compare")
    if not d.get("available"):
        return f"I can't explain that period. {d.get('reason', '')}"
    if not c:
        return _report_text(d, "")
    name = METRIC_NAMES.get(metric, metric)
    when, then = _when(d["label"]), c["label"]
    lines = []
    if metric in ("sales",):
        lines.append(f"Sales {when} were {m(d['sales'])}, against {m(c['sales'])} {then if then.startswith('the') else 'in ' + then}.")
        lines.append(f"Per trading day: {m(d['average_sales_per_trading_day'])} against "
                     f"{m(c['average_sales_per_trading_day'])}, over {d['trading_days']} and {c['trading_days']} trading days.")
        return " ".join(lines)
    if metric in d["costs"] and metric not in ("tax",) or metric in ("payroll",):
        now, before = d["costs"].get(metric, 0.0), c["costs"].get(metric, 0.0)
        lines.append(f"{name} {when} were {m(now)}, against {m(before)} {'in ' + then if then[:1].isupper() else then}.")
        if d["sales"] and c["sales"]:
            lines.append(f"As a share of sales: {100 * now / d['sales']:.0f}% against {100 * before / c['sales']:.0f}%.")
            if now / d["sales"] > before / c["sales"] and d["sales"] < c["sales"]:
                lines.append("Part of the rise in share is sales falling, not only the cost going up.")
        if metric == "payroll":
            lines.append("Wages are paid every two weeks, so a month with three pay runs looks higher.")
        return " ".join(lines)
    # profit or net cash: break the change into its drivers
    key = "operating_profit" if metric == "profit" else "net_cash"
    label = "Operating profit" if metric == "profit" else "Net cash flow"
    lines.append(f"{label} {when} was {m(d[key])}, against {m(c[key])} {'in ' + then if then[:1].isupper() else then}.")
    drivers = [("sales", d["sales"] - c["sales"])]
    cats = set(d["costs"]) | set(c["costs"])
    for k in cats:
        if metric == "profit" and k == "tax":
            continue
        drivers.append((k, -(d["costs"].get(k, 0.0) - c["costs"].get(k, 0.0))))
    drivers = [x for x in drivers if abs(x[1]) >= 50]
    drivers.sort(key=lambda x: x[1])
    parts = []
    for k, v in drivers[:4]:
        nm = "sales" if k == "sales" else analysis.COST_LABELS.get(k, k)
        if k == "sales":
            parts.append(f"sales {'fell' if v < 0 else 'rose'} by {m(abs(v))}")
        else:
            parts.append(f"{nm} {'rose' if v < 0 else 'fell'} by {m(abs(v))}")
    if parts:
        lines.append("Biggest reasons: " + "; ".join(parts) + ".")
    if d["days"] < 28 or c["days"] < 28:
        lines.append("Rent and wages land on single days, so part months swing a lot.")
    return " ".join(lines)


CHART_TEXT = ("The chart shows your bank balance over the next 60 days. The blue line is the expected path. The "
              "shaded band is the range where 8 in 10 simulated futures land, so the balance will most likely be "
              "inside it. The dashed line is your safety buffer and the red line is zero. When the band dips under "
              "the buffer or zero, that is the risk the alert talks about.")


def _coverage_text(d: dict) -> str:
    m = lambda x: money(x, d.get("currency_symbol", "£"))
    first, last = (fmt_day(_date.fromisoformat(d[k])) for k in ("first_day", "last_day"))
    cats = ", ".join(f"{analysis.COST_LABELS.get(k, k)} ({v})" for k, v in d["categories"].items())
    out = [f"Your books cover {first} to {last}: {d['days']} days and {d['transactions']} transactions.",
           f"Recorded types: {cats}."]
    if d["commitments"]:
        out.append("Upcoming bills you have told me about: " + "; ".join(
            f"{c['label']} {m(c['amount'])} on {fmt_day(_date.fromisoformat(c['date']))}" for c in d["commitments"])
            + ".")
    out.append("To add data, add rows to data/transactions.csv (date, category, counterparty, amount, memo) and "
               "bills to data/commitments.json, then restart the dashboard. Customer counts, stock levels and "
               "sales by product are not in the books, so I can't report them.")
    return " ".join(out)


def _sme_text(region: str) -> str:
    if region == "CN":
        base = ("SME means small or medium-sized enterprise. In China the limits are set industry by industry, "
                "using staff numbers and revenue, so a shop and a factory have different thresholds.")
    else:
        base = ("SME means small or medium-sized enterprise. In UK government statistics that is a business with "
                "fewer than 250 employees: micro (0 to 9), small (10 to 49) and medium (50 to 249). Lenders and "
                "grant schemes often add their own turnover limits, so check each scheme's rules.")
    return (base + " I work on one SME's own books. Ask me about its sales, costs, profit, cash forecast or "
                   "financing, for example \"sales this month\".")


def _unclear_text(message: str, parsed) -> str:
    shown = message.strip().replace("\n", " ")
    shown = shown if len(shown) <= 60 else shown[:57] + "..."
    return (f"I'm not sure what you mean by \"{shown}\". Do you want figures for a period, the cash forecast, "
            f"or financing options? For example: \"sales today\", \"costs last month\", \"profit this year\", "
            f"\"will I run out of cash?\" or \"what financing fits?\". Type \"help\" for the full list.")


def _corrections_note(fixes) -> str:
    real = [(a, b) for a, b in fixes if not (a.startswith(b) or b.startswith(a))]
    if not real:
        return ""
    return "(I read " + ", ".join(f"\"{a}\" as \"{b}\"" for a, b in real[:3]) + ".)"


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
            last = self.fallback.last
            return Reply(self._reply_without_tools(message), suggestions=list(getattr(last, "suggestions", []) or []))

        pending, lines = [], []
        for r in results:
            if r.denied and r.tool == "transfer_funds":
                lines.append("Moving money is never allowed for me, and nobody can switch that on. Payments stay "
                             "with you, in your banking app. I can show what is due (\"what bills are coming up?\") "
                             "or draft a request to a supplier for more time to pay.")
            elif r.denied:
                lines.append(f"I can't do that. {r.error}")
            elif not r.ok:
                lines.append(f"That did not work. {r.error}")
            elif r.tool == "forecast_cash" and self.fallback.last is not None and llm_text is None \
                    and self.fallback.last.intent == "spend_check":
                lines.append(_spend_check_text(r.data))
            elif r.tool == "forecast_cash":
                ahead = any(x.tool == "period_report" and x.ok and x.data.get("available") is False
                            for x in results)
                if ahead:
                    lines.append(f"What the forecast says instead: about "
                                 f"{money(r.data['summary']['projected_sales_next_28d'], self.ctx.symbol)} of sales "
                                 f"is expected over the next 28 days.")
                lines.append(_forecast_text(r.data))
            elif r.tool == "explain_shortfall":
                after_forecast = any(x.tool == "forecast_cash" and x.ok for x in results)
                lines.append(r.data["detail"] if after_forecast and r.data["detail"] else r.data["headline"] + " " + r.data["detail"])
            elif r.tool == "analyse_business":
                last = self.fallback.last if llm_text is None else None
                if last is not None and last.intent == "months":
                    lines.append(_months_text(r.data, last.metric))
                else:
                    finds = list(r.data["findings"])
                    if last is not None and {"breakeven", "break"} & set(last.tokens):
                        finds.sort(key=lambda f: not f.startswith(("Break-even", "The forecast expects")))
                    lines.append(_friendly(" ".join(finds)) if finds else r.data["narrative"])
            elif r.tool == "match_financing":
                lines.append(_match_text(r.data))
            elif r.tool == "check_plan":
                lines.append(_plan_text(r.data))
            elif r.tool == "period_report":
                last = self.fallback.last if llm_text is None else None
                if last is not None and last.intent == "why_change":
                    lines.append(_why_text(r.data))
                else:
                    lines.append(_report_text(r.data, self.ctx.ledger.as_of.isoformat()))
            elif r.tool == "data_coverage":
                lines.append(_coverage_text(r.data))
            elif r.tool == "upcoming_bills":
                lines.append(_bills_text(r.data))
            elif r.tool == "health_check":
                last = self.fallback.last if llm_text is None else None
                if last is not None and last.intent == "hire":
                    lines.append(_hire_text(r.data))
                else:
                    text_h = _health_text(r.data)
                    if last is not None and last.label == "worried":
                        text_h = ("That's understandable, so let's look at the facts together. " + text_h)
                    lines.append(text_h)
            elif r.tool == "advice":
                lines.append(_advice_text(r.data))
            elif r.tool == "what_if":
                lines.append(_what_if_text(r.data))
            elif r.tool == "weekday_sales":
                last = self.fallback.last if llm_text is None else None
                data = dict(r.data)
                if last is not None and {"close", "closing", "shut", "open"} & set(last.tokens):
                    data["closing"] = True
                lines.append(_weekday_text(data))
            elif r.tool == "weekly_sales":
                lines.append(_weeks_text(r.data))
            elif r.tool == "balance_on_date":
                lines.append(_balance_on_text(r.data))
            elif r.tool == "unusual_transactions":
                lines.append(_unusual_text(r.data))
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
        if llm_text is None and self.fallback.last is not None:
            fix = _corrections_note(self.fallback.last.corrections)
            if fix:
                text = fix + " " + text
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
        return Reply(_friendly(text or HELP), results, pending,
                     list(getattr(self.fallback.last, "suggestions", []) or []) if llm_text is None else [])

    def _run_rules(self, message: str) -> list:
        """Rule planner path. After a financing match, the plan is tested against the forecast."""
        results = [self.run_tool(n, a) for n, a in self.fallback.plan(message, self)]
        for r in list(results):
            if r.tool == "match_financing" and r.ok and r.data.get("recommendation"):
                rec, n = r.data["recommendation"], r.data["need"]
                results.append(self.run_tool("check_plan", {"product_id": rec["id"], "amount": n["amount"],
                                                            "term_months": rec.get("term_months", n["term_months"])}))
        return results

    def _reply_without_tools(self, message: str) -> str:
        """Replies for messages that need no tool: greetings, help, SME questions, data the books do not
        hold, a supplier delay with no supplier named, and anything unclear (a question back, not a menu)."""
        p = self.fallback.last
        if p is None:
            return HELP
        shop = self.ctx.ledger.profile.get("name", "your business")
        if p.intent == "greeting":
            return (f"Hello. I'm watching the cash for {shop}. Ask about sales, costs, profit, the cash forecast "
                    f"or financing, for example \"sales this month\" or \"will I run out of cash?\"")
        if p.intent == "thanks":
            return "You're welcome. Ask again any time."
        if p.intent in ("help", "empty"):
            return HELP
        if p.intent == "sme_info":
            text = _sme_text(self.region["code"])
            tokens = set(p.tokens)
            if {"how", "many"} <= tokens or "number" in tokens:
                text = ("I don't hold national statistics. For the UK, the Department for Business and Trade "
                        "publishes the count every year in its Business population estimates. ") + text
            elif {"my", "business"} <= tokens or "am" in tokens or "i" in tokens:
                rev = self.ctx.ledger.profile.get("annual_revenue")
                if rev:
                    text = (f"Very likely yes: the UK definition uses staff numbers, which your books don't record, "
                            f"but a business with yearly revenue of {money(float(rev), self.ctx.symbol)} is far below "
                            f"the size of a large company. ") + text
            return text
        if p.intent == "identity":
            return (f"I'm Triody, a finance copilot for {shop}. I read the books, forecast cash, explain what is "
                    f"happening and prepare drafts. I can't move money, and nothing is sent without your code. "
                    f"I'm software, not a person, and this is information, not regulated advice.")
        if p.intent == "late_tax":
            return _late_tax_text(self.region["code"], self.ctx.business.tax_authority)
        if p.intent == "chart":
            return CHART_TEXT
        if p.intent == "code_request":
            return ("I never see approval codes and I can't show, guess or skip them. Each code goes only to your "
                    "owner device, works once, for one draft, for 10 minutes. My rules can't be switched off from "
                    "the chat.")
        if p.intent == "off_topic":
            return (f"That's outside what I do. I only work with the money side of {shop}: sales, costs, profit, "
                    f"cash, bills and financing. Try one of the suggestions, or type \"help\".")
        if p.intent == "bye":
            return "Bye for now. I'll run the daily check again next time you open me."
        if p.intent == "glossary":
            from .intent import GLOSSARY, GLOSSARY_EXAMPLES
            name, text = GLOSSARY[p.term]
            if self.region["code"] != "UK":
                text = text.replace("HMRC", self.ctx.business.tax_authority)
            ex = GLOSSARY_EXAMPLES.get(p.term)
            return text + (f" To see yours, ask \"{ex}\"" if ex else "")
        if p.intent == "unsupported":
            cats = sorted({t.category for t in self.ctx.ledger.txns})
            names = ", ".join(analysis.COST_LABELS.get(c, c) for c in cats)
            return (f"Your books don't record {p.unsupported}, so I can't answer that from your data. They hold "
                    f"dated money in and out: {names}. I can give any of those for a period, for example "
                    f"\"sales last week\" or \"wages this month\".")
        if p.intent == "supplier_delay":
            known = sorted({t.counterparty for t in self.ctx.ledger.txns if t.category == "supplier"})
            if not known:
                return "There are no supplier payments in your books, so there is no one to ask for a delay."
            return (f"Which supplier should I ask? Your books show: {', '.join(known)}. For example: "
                    f"\"delay {known[0]} by 7 days\".")
        note = _corrections_note(p.corrections)
        return ((note + " ") if note else "") + _unclear_text(message, p)

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
                term = rec.get("term_months", n["term_months"])
                plan = self.run_tool("check_plan", {"product_id": rec["id"], "amount": n["amount"],
                                                    "term_months": term})
                results.append(plan)
                if plan.ok:
                    lines.append(_plan_text(plan.data))
                draft = self.run_tool("draft_financing_application",
                                      {"product_id": rec["id"], "amount": n["amount"], "term_months": term})
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
