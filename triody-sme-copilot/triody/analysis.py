"""Business financial analysis. Every number comes from the ledger or the forecast.

explain_shortfall   why cash is short: the last 28 days against the 28 before, plus what the forecast adds
business_analysis   how the business is doing month by month: margin, cost trends, the biggest suppliers,
                    break-even sales, and a month-on-month comparison
"""
import statistics
from datetime import date, timedelta

CATEGORY_LABELS = {"supplier": "supplier bills", "payroll": "wages", "rent": "rent",
                   "utilities": "utilities", "packaging": "packaging", "loan": "loan repayments",
                   "tax": "tax"}


def _window(ledger, end: date, days: int = 28) -> dict:
    start = end - timedelta(days=days - 1)
    out = {"start": start.isoformat(), "end": end.isoformat(),
           "sales": ledger.total(start, end, "sales"), "net": ledger.total(start, end)}
    for cat in CATEGORY_LABELS:
        out[cat] = -ledger.total(start, end, cat)
    return out


_SYMBOL = {"value": "£"}


def _gbp(x: float) -> str:
    """Format an amount without sign. The name is historical: the symbol follows the shop's currency."""
    return f"{_SYMBOL['value']}{abs(x):,.0f}"


def _use_currency(ledger):
    _SYMBOL["value"] = ledger.profile.get("currency_symbol", "£")


def explain_shortfall(ledger, fc) -> dict:
    """Compare the last 28 days with the 28 days before, then list what the forecast adds."""
    _use_currency(ledger)
    asof = fc.asof
    cur = _window(ledger, asof)
    prev = _window(ledger, asof - timedelta(days=28))
    drivers = []

    sales_change = cur["sales"] - prev["sales"]
    drivers.append({"name": "Sales", "impact_gbp": round(sales_change, 2),
                    "detail": f"Sales were {_gbp(cur['sales'])} in the last 28 days, "
                              f"{_gbp(sales_change)} {'lower' if sales_change < 0 else 'higher'} "
                              f"than the 28 days before."})
    # Only costs that repeat every week or two are compared, so monthly bills falling on a
    # window edge do not create false changes.
    for cat in ("supplier", "payroll"):
        change = cur[cat] - prev[cat]
        if abs(change) >= 100:
            drivers.append({"name": CATEGORY_LABELS[cat].capitalize(), "impact_gbp": round(-change, 2),
                            "detail": f"{CATEGORY_LABELS[cat].capitalize()} cost {_gbp(cur[cat])} in the last "
                                      f"28 days, {_gbp(change)} {'more' if change > 0 else 'less'} than before."})

    ahead = []
    sales_drop = fc.summary["projected_sales_next_28d"] - fc.summary["actual_sales_last_28d"]
    if sales_drop < -100:
        ahead.append({"name": "Slower sales ahead", "impact_gbp": round(sales_drop, 2),
                      "detail": f"The model expects {_gbp(fc.summary['projected_sales_next_28d'])} of sales in the "
                                f"next 28 days, {_gbp(sales_drop)} less than the last 28 days."})
    for item in fc.schedule:
        if item["category"].startswith("commitment"):
            ahead.append({"name": item["counterparty"], "impact_gbp": -item["amount"],
                          "detail": f"{item['counterparty']}: {_gbp(item['amount'])} due on {item['next_dates'][0]}."})
    runs = [t for t in ledger.txns if t.category == "payroll" and t.date <= asof]
    if len(runs) >= 2 and runs[-1].amount < runs[0].amount - 50:
        rise = runs[0].amount - runs[-1].amount
        ahead.append({"name": "Higher wage bill", "impact_gbp": round(-rise, 2),
                      "detail": f"Each payroll run now costs {_gbp(runs[-1].amount)}, "
                                f"{_gbp(rise)} more than at the start of the history."})
    drivers.sort(key=lambda d: d["impact_gbp"])
    ahead.sort(key=lambda d: d["impact_gbp"])

    margin = None
    if cur["sales"] > 0:
        margin = round(100 * (cur["sales"] - cur["supplier"]) / cur["sales"], 1)
    wages_share = round(100 * cur["payroll"] / cur["sales"], 1) if cur["sales"] > 0 else None

    s = fc.summary
    lines = []
    if s["alert_level"] == "ok":
        lines.append(f"No cash problem shows in the next {len(fc.days)} days. "
                     f"The lowest expected balance is {_gbp(s['lowest_p50']['balance'])} on "
                     f"{s['lowest_p50']['date']}.")
    else:
        low = s["lowest_p50"]["balance"]
        where = f"{_gbp(low)} below zero" if low < 0 else _gbp(low)
        lines.append(f"Cash is expected to fall to {where} around {s['lowest_p50']['date']}, "
                     f"against your {_gbp(fc.buffer)} safety buffer.")
        top = ahead[:2] or drivers[:2]
        if top:
            lines.append("What drives it: " + " ".join(d["detail"] for d in top))
    if margin is not None:
        lines.append(f"Over the last 28 days, supplier bills took {100 - margin:.0f}% of sales, "
                     f"and wages took {wages_share:.0f}%.")
    return {"headline": lines[0], "detail": " ".join(lines[1:]),
            "windows": {"last_28d": cur, "previous_28d": prev},
            "drivers_past": drivers, "drivers_ahead": ahead,
            "gross_margin_after_suppliers_pct": margin, "wages_share_of_sales_pct": wages_share,
            "narrative": " ".join(lines)}


# ----------------------------------------------------------------------------- business analysis
VARIABLE_COSTS = ("supplier", "packaging")          # move with sales
FIXED_COSTS = ("payroll", "rent", "utilities", "loan", "fees")   # do not move with sales in the short run
COST_LABELS = {**CATEGORY_LABELS, "fees": "card and bank fees"}


def _month_rows(ledger, asof: date) -> list:
    """One row per calendar month in the history. Only full months are marked full."""
    first = min(t.date for t in ledger.txns)
    rows, m = [], date(first.year, first.month, 1)
    while m <= asof:
        nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        start, end = max(m, first), min(nxt - timedelta(days=1), asof)
        row = {"month": m.strftime("%Y-%m"), "days": (end - start).days + 1,
               "full": start == m and end == nxt - timedelta(days=1),
               "sales": round(ledger.total(start, end, "sales"), 2)}
        for cat in VARIABLE_COSTS + FIXED_COSTS + ("tax",):
            row[cat] = round(-ledger.total(start, end, cat), 2)
        row["variable_costs"] = round(sum(row[c] for c in VARIABLE_COSTS), 2)
        row["fixed_costs"] = round(sum(row[c] for c in FIXED_COSTS), 2)
        row["gross_profit"] = round(row["sales"] - row["variable_costs"], 2)
        row["operating_profit"] = round(row["gross_profit"] - row["fixed_costs"], 2)
        row["gross_margin_pct"] = round(100 * row["gross_profit"] / row["sales"], 1) if row["sales"] else None
        row["wages_pct_of_sales"] = round(100 * row["payroll"] / row["sales"], 1) if row["sales"] else None
        row["net_cash"] = round(ledger.total(start, end), 2)
        rows.append(row)
        m = nxt
    return rows


def _pct_change(new: float, old: float):
    return None if not old else round(100 * (new - old) / old, 1)


def business_analysis(ledger, fc=None) -> dict:
    """Monthly figures, cost trends, the biggest suppliers, break-even, and plain-language findings."""
    _use_currency(ledger)
    asof = fc.asof if fc else ledger.as_of
    rows = _month_rows(ledger, asof)
    full = [r for r in rows if r["full"]]
    findings = []

    # Month on month: the last full month against the one before.
    mom = None
    if len(full) >= 2:
        a, b = full[-1], full[-2]
        mom = {"month": a["month"], "previous": b["month"],
               "sales_change_pct": _pct_change(a["sales"], b["sales"]),
               "operating_profit": a["operating_profit"], "previous_operating_profit": b["operating_profit"],
               "gross_margin_pct": a["gross_margin_pct"], "previous_gross_margin_pct": b["gross_margin_pct"]}
        word = "down" if a["sales"] < b["sales"] else "up"
        findings.append(f"Sales in {a['month']} were {_gbp(a['sales'])}, {word} {abs(mom['sales_change_pct']):.1f}% "
                        f"on {b['month']}. Operating profit went from "
                        f"{'-' if b['operating_profit'] < 0 else ''}{_gbp(b['operating_profit'])} to "
                        f"{'-' if a['operating_profit'] < 0 else ''}{_gbp(a['operating_profit'])}.")

    # Cost trends: each cost line as a share of sales, first full month against the last.
    trends = []
    if len(full) >= 2:
        first_m, last_m = full[0], full[-1]
        for cat in VARIABLE_COSTS + FIXED_COSTS:
            if first_m["sales"] and last_m["sales"] and (first_m[cat] or last_m[cat]):
                was, now = 100 * first_m[cat] / first_m["sales"], 100 * last_m[cat] / last_m["sales"]
                trends.append({"category": cat, "label": COST_LABELS.get(cat, cat),
                               "from_month": first_m["month"], "to_month": last_m["month"],
                               "share_of_sales_from_pct": round(was, 1), "share_of_sales_to_pct": round(now, 1),
                               "amount_change_pct": _pct_change(last_m[cat], first_m[cat])})
        trends.sort(key=lambda t: t["share_of_sales_to_pct"] - t["share_of_sales_from_pct"], reverse=True)
        top = trends[0] if trends else None
        if top and top["share_of_sales_to_pct"] - top["share_of_sales_from_pct"] >= 2:
            findings.append(f"{top['label'].capitalize()} grew fastest against sales: "
                            f"{top['share_of_sales_from_pct']:.0f}% of sales in {top['from_month']}, "
                            f"{top['share_of_sales_to_pct']:.0f}% in {top['to_month']}.")

    # Biggest suppliers and payees over the last 90 days.
    start90 = asof - timedelta(days=89)
    spend = {}
    for t in ledger.between(start90, asof):
        if t.amount < 0 and t.category != "tax":
            key = (t.counterparty, t.category)
            spend[key] = spend.get(key, 0.0) - t.amount
    total_out = sum(spend.values())
    sales90 = ledger.total(start90, asof, "sales")
    payees = [{"counterparty": k[0], "category": k[1], "amount": round(v, 2),
               "share_of_costs_pct": round(100 * v / total_out, 1) if total_out else None,
               "per_100_of_sales": round(100 * v / sales90, 1) if sales90 else None}
              for k, v in sorted(spend.items(), key=lambda kv: -kv[1])][:5]
    suppliers = [p for p in payees if p["category"] in VARIABLE_COSTS]
    if suppliers:
        s0 = suppliers[0]
        findings.append(f"Your biggest supplier is {s0['counterparty']}: {_gbp(s0['amount'])} in the last 90 days, "
                        f"{s0['share_of_costs_pct']:.0f}% of all costs, or {_gbp(s0['per_100_of_sales'])} for every "
                        f"{_gbp(100)} of sales.")

    # Break-even: the daily sales that cover fixed costs at the current variable-cost ratio.
    breakeven = None
    recent = full[-2:] if len(full) >= 2 else full
    if recent:
        sales_r = sum(r["sales"] for r in recent)
        days_r = sum(r["days"] for r in recent)
        var_ratio = sum(r["variable_costs"] for r in recent) / sales_r if sales_r else None
        fixed_daily = sum(r["fixed_costs"] for r in recent) / days_r
        if var_ratio is not None and var_ratio < 1:
            be = fixed_daily / (1 - var_ratio)
            last28 = ledger.total(asof - timedelta(days=27), asof, "sales") / 28
            breakeven = {"daily_sales_needed": round(be, 2), "variable_cost_ratio_pct": round(100 * var_ratio, 1),
                         "fixed_costs_per_day": round(fixed_daily, 2), "actual_daily_sales_last_28d": round(last28, 2),
                         "based_on": [r["month"] for r in recent]}
            if fc is not None:
                breakeven["projected_daily_sales_next_28d"] = round(fc.summary["projected_sales_next_28d"] / 28, 2)
            gap = last28 - be
            findings.append(f"Break-even is about {_gbp(be)} of sales a day (fixed costs {_gbp(fixed_daily)} a day, "
                            f"suppliers and packaging take {100 * var_ratio:.0f}% of sales). The last 28 days averaged "
                            f"{_gbp(last28)} a day, {_gbp(gap)} {'above' if gap >= 0 else 'below'} break-even.")
            if fc is not None:
                ahead = breakeven["projected_daily_sales_next_28d"] - be
                findings.append(f"The forecast expects {_gbp(breakeven['projected_daily_sales_next_28d'])} a day over the "
                                f"next 28 days, {_gbp(ahead)} {'above' if ahead >= 0 else 'below'} break-even.")
    return {"currency_symbol": _SYMBOL["value"], "months": rows, "month_on_month": mom, "cost_trends": trends,
            "top_payees_90d": payees, "breakeven": breakeven, "findings": findings,
            "narrative": " ".join(findings) if findings else "There is not enough history for a monthly analysis.",
            "notes": ["Gross profit is sales minus supplier and packaging bills. Operating profit also takes off "
                      "wages, rent, utilities, loan repayments and fees. Tax is shown apart.",
                      "Months at the start and end of the history are partial and are left out of trends."]}
