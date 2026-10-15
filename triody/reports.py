"""Figures for a period the owner names: today, this month, a quarter, a date range.

Every number comes straight from the ledger. A period that runs past the last day in the books, or
starts before the first one, is cut to the days the books cover, and the result says so. A period
that has no recorded days at all comes back with available=False and a plain reason, never zeros.
"""
from datetime import date, timedelta

from .analysis import COST_LABELS, FIXED_COSTS, VARIABLE_COSTS
from .intent import fmt_day

COST_CATEGORIES = VARIABLE_COSTS + FIXED_COSTS + ("tax",)
METRICS = ("summary", "sales", "costs", "supplier", "payroll", "rent", "utilities", "packaging", "fees", "tax",
           "loan", "profit", "net_cash", "transactions", "average", "best_day", "worst_day", "top_payees", "top_suppliers", "weekdays", "payee", "trading_days")


def coverage(ledger) -> dict:
    """What the books hold: first and last day, size, and the categories recorded."""
    first = min(t.date for t in ledger.txns) if ledger.txns else ledger.as_of
    cats = {}
    for t in ledger.txns:
        cats[t.category] = cats.get(t.category, 0) + 1
    return {"first_day": first.isoformat(), "last_day": ledger.as_of.isoformat(),
            "days": (ledger.as_of - first).days + 1, "transactions": len(ledger.txns),
            "categories": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
            "commitments": [{"date": c["date"], "label": c["label"], "amount": float(c["amount"])}
                            for c in ledger.commitments],
            "shop": ledger.profile.get("name", ""), "currency_symbol": ledger.profile.get("currency_symbol", "£")}


def _figures(ledger, start: date, end: date) -> dict:
    rows = ledger.between(start, end)
    days = (end - start).days + 1
    sales_by_day = ledger.daily(start, end, "sales")
    costs = {c: round(-sum(t.amount for t in rows if t.category == c and t.amount < 0), 2) for c in COST_CATEGORIES}
    other_out = -sum(t.amount for t in rows if t.amount < 0 and t.category not in COST_CATEGORIES)
    if other_out:
        costs["other"] = round(other_out, 2)
    sales = sum(sales_by_day.values())
    variable = sum(costs[c] for c in VARIABLE_COSTS)
    fixed = sum(costs[c] for c in FIXED_COSTS)
    money_in = sum(t.amount for t in rows if t.amount > 0)
    money_out = -sum(t.amount for t in rows if t.amount < 0)
    open_days = {d: v for d, v in sales_by_day.items() if v > 0}
    best = max(open_days.items(), key=lambda kv: kv[1]) if open_days else None
    worst = min(open_days.items(), key=lambda kv: kv[1]) if open_days else None
    spend = {}
    for t in rows:
        if t.amount < 0:
            key = (t.counterparty, t.category)
            spend[key] = spend.get(key, 0.0) - t.amount
    payees = [{"counterparty": k[0], "category": k[1], "amount": round(v, 2),
               "share_pct": round(100 * v / money_out, 1) if money_out else None}
              for k, v in sorted(spend.items(), key=lambda kv: -kv[1])[:8]]
    return {
        "start": start.isoformat(), "end": end.isoformat(), "days": days, "payees": payees,
        "sales": round(sales, 2), "trading_days": len(open_days),
        "average_daily_sales": round(sales / days, 2), "average_daily_costs": round(money_out / days, 2),
        "average_sales_per_trading_day": round(sales / len(open_days), 2) if open_days else 0.0,
        "best_day": {"date": best[0].isoformat(), "sales": round(best[1], 2)} if best else None,
        "worst_day": {"date": worst[0].isoformat(), "sales": round(worst[1], 2)} if worst else None,
        "costs": costs, "total_costs": round(money_out, 2), "variable_costs": round(variable, 2),
        "gross_profit": round(sales - variable, 2), "operating_profit": round(sales - variable - fixed, 2),
        "gross_margin_pct": round(100 * (sales - variable) / sales, 1) if sales else None,
        "money_in": round(money_in, 2), "money_out": round(money_out, 2), "net_cash": round(money_in - money_out, 2),
        "transactions": len(rows), "payments_out": sum(1 for t in rows if t.amount < 0),
    }


def _clip(ledger, start: date, end: date):
    """Cut a period to the days the books cover. Returns (start, end, notes) or (None, None, reason)."""
    first = min(t.date for t in ledger.txns) if ledger.txns else ledger.as_of
    last = ledger.as_of
    if start > end:
        return None, None, "The start of that period is after its end."
    if start > last:
        return None, None, (f"That period has not happened yet in your books. They run to {fmt_day(last)}. "
                            f"I can forecast it instead.")
    if end < first:
        return None, None, (f"Your books start on {fmt_day(first)}, so there is nothing recorded for that period. "
                            f"Add older transactions to the ledger to cover it.")
    notes = []
    if start < first:
        notes.append(f"Your books start on {fmt_day(first)}, so this covers {fmt_day(first)} onwards only.")
        start = first
    if end > last:
        notes.append(f"Your books run to {fmt_day(last)}, so this covers the days up to then.")
        end = last
    return start, end, notes


def period_report(ledger, start: date, end: date, label: str = "", compare=None, counterparty: str = None) -> dict:
    """Figures for one period, with an optional comparison period (start, end, label)."""
    s, e, notes = _clip(ledger, start, end)
    sym = ledger.profile.get("currency_symbol", "£")
    if s is None:
        return {"available": False, "label": label or f"{fmt_day(start)} to {fmt_day(end)}", "reason": notes,
                "requested": {"start": start.isoformat(), "end": end.isoformat()}, "currency_symbol": sym}
    out = {"available": True, "label": label or f"{fmt_day(s)} to {fmt_day(e)}", "notes": notes,
           "partial": bool(notes), "currency_symbol": sym, **_figures(ledger, s, e)}
    if counterparty:
        rows = [t for t in ledger.between(s, e) if t.counterparty == counterparty]
        out["counterparty"] = {"name": counterparty, "paid": round(-sum(t.amount for t in rows if t.amount < 0), 2),
                               "received": round(sum(t.amount for t in rows if t.amount > 0), 2),
                               "payments": sum(1 for t in rows if t.amount < 0),
                               "last": rows[-1].date.isoformat() if rows else None}
    if compare:
        cs, ce, cnotes = _clip(ledger, compare[0], compare[1])
        if cs is not None:
            out["compare"] = {"label": compare[2], "notes": cnotes, **_figures(ledger, cs, ce)}
    return out


def standard_periods(today: date) -> list:
    """The periods shown when the owner asks for a figure without naming a period."""
    monday = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    return [(today, today, f"Today ({fmt_day(today)})"),
            (monday, today, "This week so far"),
            (month_start, today, f"{today.strftime('%B')} so far"),
            (last_month_end.replace(day=1), last_month_end, last_month_end.strftime("%B %Y")),
            (today.replace(month=1, day=1), today, f"{today.year} so far")]


DAILY_METRICS = ("summary", "sales", "net_cash", "transactions", "average", "best_day", "worst_day")


def overview(ledger, metric: str = "summary") -> dict:
    """Standard periods for a figure asked without a period. Monthly bills (rent, wages, tax) are not shown
    for today and this week, where they would mostly read zero; a monthly average is shown instead."""
    today = ledger.as_of
    periods = standard_periods(today)
    if metric not in DAILY_METRICS:
        periods = periods[2:]
    rows = [period_report(ledger, s, e, label) for s, e, label in periods]
    if metric not in DAILY_METRICS:
        first = min((t.date for t in ledger.txns), default=today)
        m = date(first.year, first.month, 1)
        if m < first:
            m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        month_start = today.replace(day=1)
        if m < month_start:
            r = period_report(ledger, m, month_start - timedelta(days=1), "Average full month")
            n = (month_start.year - m.year) * 12 + month_start.month - m.month
            for k in ("sales", "total_costs", "operating_profit", "net_cash", "gross_profit", "money_in", "money_out"):
                r[k] = round(r[k] / n, 2)
            r["costs"] = {k: round(v / n, 2) for k, v in r["costs"].items()}
            r["transactions"] = round(r["transactions"] / n)
            r["label"] = f"Average full month ({n} months)"
            rows.append(r)
    return {"overview": rows, "as_of": today.isoformat(), "currency_symbol": ledger.profile.get("currency_symbol", "£")}


def weekdays(ledger, start: date, end: date) -> dict:
    """Sales by day of the week over a period: total, trading days and the average trading day."""
    s, e, notes = _clip(ledger, start, end)
    if s is None:
        return {"available": False, "reason": notes}
    by_day = ledger.daily(s, e, "sales")
    rows = []
    for wd in range(7):
        vals = [v for d, v in by_day.items() if d.weekday() == wd]
        open_vals = [v for v in vals if v > 0]
        rows.append({"weekday": wd, "name": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
                                             "Sunday")[wd],
                     "days": len(vals), "trading_days": len(open_vals), "sales": round(sum(vals), 2),
                     "average": round(sum(open_vals) / len(open_vals), 2) if open_vals else 0.0})
    open_rows = [r for r in rows if r["trading_days"]]
    overall = sum(r["sales"] for r in open_rows) / max(1, sum(r["trading_days"] for r in open_rows))
    return {"available": True, "start": s.isoformat(), "end": e.isoformat(), "notes": notes, "rows": rows,
            "average_trading_day": round(overall, 2),
            "best": max(open_rows, key=lambda r: r["average"])["name"] if open_rows else None,
            "worst": min(open_rows, key=lambda r: r["average"])["name"] if open_rows else None,
            "closed": [r["name"] for r in rows if r["days"] and not r["trading_days"]]}


def weeks(ledger, start: date = None, end: date = None) -> dict:
    """Sales and net cash for each full Monday-to-Sunday week in a period (default the last 12 weeks)."""
    last_sunday = ledger.as_of - timedelta(days=(ledger.as_of.weekday() + 1) % 7)
    end = min(end or last_sunday, last_sunday)
    start = start or end - timedelta(days=12 * 7 - 1)
    first = min((t.date for t in ledger.txns), default=ledger.as_of)
    monday = start - timedelta(days=start.weekday())
    if monday < start:
        monday += timedelta(days=7)
    rows = []
    while monday + timedelta(days=6) <= end:
        if monday >= first:
            f = _figures(ledger, monday, monday + timedelta(days=6))
            rows.append({"start": monday.isoformat(), "end": (monday + timedelta(days=6)).isoformat(),
                         "sales": f["sales"], "net_cash": f["net_cash"], "costs": f["total_costs"]})
        monday += timedelta(days=7)
    if not rows:
        return {"available": False, "reason": "There is no full week in that period."}
    avg = sum(r["sales"] for r in rows) / len(rows)
    return {"available": True, "rows": rows, "average_sales": round(avg, 2),
            "best": max(rows, key=lambda r: r["sales"]), "worst": min(rows, key=lambda r: r["sales"]),
            "currency_symbol": ledger.profile.get("currency_symbol", "£")}
