"""Answers that combine several sources: bills coming up, a health check, advice, and what-if tests.

Every figure comes from the ledger, the owner's commitments, the forecast or the monthly analysis.
Advice is a ranked list of actions, each with the number behind it, so the owner can check it.
"""
from datetime import date, timedelta

from . import analysis, forecast as fc_mod, region as region_mod

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# ----------------------------------------------------------------------------- bills coming up
def upcoming_bills(ctx, days: int = 60, focus: str = "") -> dict:
    """Owner commitments and recurring payments found in the ledger, due in the next `days` days."""
    fc = ctx.ensure_forecast()
    asof = ctx.ledger.as_of
    end = asof + timedelta(days=days)
    items = []
    for c in ctx.ledger.commitments:
        d = date.fromisoformat(c["date"])
        if asof < d:
            items.append({"date": d.isoformat(), "label": c["label"], "amount": round(float(c["amount"]), 2),
                          "source": "you told me", "tax": region_mod.is_tax(c, ctx.region), "inside": d <= end})
    for r in fc.schedule:
        if r["category"].startswith("commitment"):
            continue
        for ds in r["next_dates"]:
            d = date.fromisoformat(ds)
            if asof < d <= end:
                items.append({"date": ds, "label": f"{r['counterparty']} ({analysis.COST_LABELS.get(r['category'], r['category'])})",
                              "amount": round(r["amount"], 2), "source": f"repeats {r['cadence']}",
                              "estimate": r["linked_to_sales"], "tax": r["category"] == "tax", "inside": True})
    items.sort(key=lambda x: x["date"])
    by_date = {d["date"]: d for d in fc.days}
    for i in items:
        day = by_date.get(i["date"])
        if day:
            i["expected_balance_after"] = day["p50"]
            i["chance_below_zero"] = day["prob_below_zero"]
    inside = [i for i in items if i["inside"]]
    match = []
    if focus:
        f = focus.lower()
        match = [i for i in items if f in i["label"].lower() or (f == "tax" and i["tax"])]
    return {"days": days, "end": end.isoformat(), "items": inside, "focus": focus, "matches": match[:3],
            "later_tax": [i for i in items if i["tax"] and not i["inside"]][:2],
            "total": round(sum(i["amount"] for i in inside), 2),
            "tax_total": round(sum(i["amount"] for i in inside if i["tax"]), 2),
            "balance": round(ctx.ledger.balance_on(asof), 2), "currency_symbol": ctx.symbol,
            "lowest": fc.summary["lowest_p50"], "buffer": fc.buffer}


# ----------------------------------------------------------------------------- health check
def _full_months(ctx):
    a = analysis.business_analysis(ctx.ledger, ctx.ensure_forecast())
    return a, [r for r in a["months"] if r["full"]]


def health_check(ctx) -> dict:
    fc = ctx.ensure_forecast()
    s = fc.summary
    a, full = _full_months(ctx)
    checks = []
    m = ctx.money

    def add(area, status, detail):
        checks.append({"area": area, "status": status, "detail": detail})

    if s["alert_level"] == "critical":
        neg = s["first_negative_date_p50"]
        add("Cash, next 60 days", "problem",
            f"{s['probability_negative_any_day']:.0%} chance of going below zero. "
            + (f"On the expected path cash runs out around {neg}, " if neg else "")
            + f"with a low of {m(s['lowest_p50']['balance'])} on {s['lowest_p50']['date']}.")
    elif s["alert_level"] == "warning":
        add("Cash, next 60 days", "watch",
            f"Cash stays above zero on the expected path, but the risk of dropping under your {m(fc.buffer)} buffer "
            f"reaches 25% on {s['first_risk_date']}.")
    else:
        add("Cash, next 60 days", "good", f"The expected low is {m(s['lowest_p50']['balance'])}, above your "
                                          f"{m(fc.buffer)} buffer.")
    if full:
        last = full[-1]
        if last["operating_profit"] < 0:
            add("Profit", "problem", f"Operating loss of {m(-last['operating_profit'])} in {last['month']}.")
        elif len(full) >= 2 and full[-2]["operating_profit"] > 0 and \
                last["operating_profit"] < 0.7 * full[-2]["operating_profit"]:
            add("Profit", "watch", f"Operating profit fell from {m(full[-2]['operating_profit'])} in "
                                   f"{full[-2]['month']} to {m(last['operating_profit'])} in {last['month']}.")
        else:
            add("Profit", "good", f"Operating profit of {m(last['operating_profit'])} in {last['month']}.")
        if len(full) >= 2:
            ch = 100 * (last["sales"] - full[-2]["sales"]) / full[-2]["sales"] if full[-2]["sales"] else 0
            add("Sales trend", "watch" if ch <= -10 else "good",
                f"Sales in {last['month']} were {m(last['sales'])}, {'down' if ch < 0 else 'up'} {abs(ch):.0f}% "
                f"on {full[-2]['month']}.")
            first = full[0]
            if last["wages_pct_of_sales"] is not None and first["wages_pct_of_sales"] is not None:
                rise = last["wages_pct_of_sales"] - first["wages_pct_of_sales"]
                status = "problem" if last["wages_pct_of_sales"] > 40 else "watch" if rise >= 3 else "good"
                add("Wages", status, f"Wages took {last['wages_pct_of_sales']:.0f}% of sales in {last['month']}, "
                                     f"against {first['wages_pct_of_sales']:.0f}% in {first['month']}.")
            if last["gross_margin_pct"] is not None and first["gross_margin_pct"] is not None:
                drop = first["gross_margin_pct"] - last["gross_margin_pct"]
                add("Gross margin", "watch" if drop >= 3 else "good",
                    f"{last['gross_margin_pct']:.0f}% in {last['month']}, against {first['gross_margin_pct']:.0f}% "
                    f"in {first['month']}.")
    be = a.get("breakeven")
    if be:
        gap = be["actual_daily_sales_last_28d"] - be["daily_sales_needed"]
        ahead = be.get("projected_daily_sales_next_28d", be["actual_daily_sales_last_28d"]) - be["daily_sales_needed"]
        status = "problem" if gap < 0 else "watch" if ahead < 0 or gap < 0.1 * be["daily_sales_needed"] else "good"
        add("Break-even", status, f"You need about {m(be['daily_sales_needed'])} of sales a day. The last 28 days "
                                  f"averaged {m(be['actual_daily_sales_last_28d'])}; the forecast expects "
                                  f"{m(be.get('projected_daily_sales_next_28d', 0))}.")
    fcf = ctx.business.monthly_free_cash_flow
    add("Free cash flow", "problem" if fcf <= 0 else "watch" if fcf < 0.05 * ctx.business.annual_revenue / 12
        else "good", f"On average {m(fcf)} a month is left after every payment, over the last six months.")
    problems = sum(1 for c in checks if c["status"] == "problem")
    watches = sum(1 for c in checks if c["status"] == "watch")
    verdict = ("under pressure" if problems >= 2 else "needs attention" if problems == 1 else
               "mostly healthy" if watches else "healthy")
    payroll = None
    if full:
        last = full[-1]
        payroll = {"month": last["month"], "wages": last["payroll"], "share": last["wages_pct_of_sales"],
                   "operating_profit": last["operating_profit"]}
    return {"verdict": verdict, "checks": checks, "problems": problems, "watch": watches,
            "free_cash_flow": round(fcf, 2), "payroll": payroll, "currency_symbol": ctx.symbol,
            "buffer": fc.buffer}


# ----------------------------------------------------------------------------- advice
def advice(ctx, focus: str = "") -> dict:
    """Ranked actions, each with the figure behind it. Rough monthly effect in `impact`."""
    fc = ctx.ensure_forecast()
    s = fc.summary
    a, full = _full_months(ctx)
    m = ctx.money
    asof = ctx.ledger.as_of
    actions = []
    tight = s["alert_level"] != "ok"

    for c in sorted(ctx.ledger.commitments, key=lambda c: c["date"]):
        d = date.fromisoformat(c["date"])
        if not (asof < d <= asof + timedelta(days=60)):
            continue
        weeks = max(1, (d - asof).days // 7)
        if region_mod.is_tax(c, ctx.region) and tight:
            actions.append({"impact": float(c["amount"]), "tag": "cash", "title": f"Spread the {c['label']}",
                            "detail": f"{m(float(c['amount']))} is due on {d.isoformat()} and it drives the shortfall. "
                                      f"Ask {ctx.business.tax_authority} for Time to Pay before the due date. Ask me "
                                      f"\"what financing fits?\" to see the plan tested."})
        else:
            actions.append({"impact": float(c["amount"]) / 3, "tag": "cash", "title": f"Put money aside for {c['label']}",
                            "detail": f"{m(float(c['amount']))} is due on {d.isoformat()}. Setting aside "
                                      f"{m(float(c['amount']) / weeks)} a week for {weeks} weeks avoids a shock."})

    start90 = asof - timedelta(days=89)
    spend = {}
    for t in ctx.ledger.between(start90, asof):
        if t.amount < 0 and t.category == "supplier":
            spend[t.counterparty] = spend.get(t.counterparty, 0.0) - t.amount
    if spend:
        top, total = max(spend.items(), key=lambda kv: kv[1])
        week = total / 90 * 7
        if tight:
            actions.append({"impact": week, "tag": "cash", "title": f"Ask {top} for 7 more days to pay",
                            "detail": f"You pay them about {m(week)} a week. One week's grace keeps that much in the "
                                      f"account through the tight weeks. Say \"delay {top} by 7 days\" and I will "
                                      f"draft the request."})
        actions.append({"impact": total / 3 * 0.05, "tag": "cost", "title": f"Renegotiate with {top}",
                        "detail": f"They cost {m(total)} in the last 90 days. Getting 5% off saves about "
                                  f"{m(total / 3 * 0.05)} a month."})

    if len(full) >= 2:
        first, last = full[0], full[-1]
        if last["wages_pct_of_sales"] and first["wages_pct_of_sales"]:
            rise = last["wages_pct_of_sales"] - first["wages_pct_of_sales"]
            if rise >= 2:
                saving = rise / 100 * last["sales"]
                actions.append({"impact": saving, "tag": "cost", "title": "Bring wages back in line with sales",
                                "detail": f"Wages went from {first['wages_pct_of_sales']:.0f}% of sales in {first['month']} "
                                          f"to {last['wages_pct_of_sales']:.0f}% in {last['month']}. Matching rotas to "
                                          f"the quieter days and getting back to {first['wages_pct_of_sales']:.0f}% "
                                          f"would save about {m(saving)} a month at current sales."})
        if last["sales"] and first["sales"]:
            var_then = 100 * first["variable_costs"] / first["sales"]
            var_now = 100 * last["variable_costs"] / last["sales"]
            if var_now - var_then >= 2:
                saving = (var_now - var_then) / 100 * last["sales"]
                actions.append({"impact": saving, "tag": "cost", "title": "Check supplier and packaging prices",
                                "detail": f"They took {var_now:.0f}% of sales in {last['month']}, against "
                                          f"{var_then:.0f}% in {first['month']}. Getting back to {var_then:.0f}% saves "
                                          f"about {m(saving)} a month, through prices, portion sizes or waste."})

    by_day = ctx.ledger.daily(asof - timedelta(days=83), asof, "sales")
    avgs = {}
    for wd in range(7):
        vals = [v for d, v in by_day.items() if d.weekday() == wd and v > 0]
        if vals:
            avgs[wd] = sum(vals) / len(vals)
    if len(avgs) >= 3:
        overall = sum(avgs.values()) / len(avgs)
        low = min(avgs, key=avgs.get)
        if avgs[low] < 0.8 * overall:
            gap = (overall - avgs[low]) * 4.3 * 0.25
            actions.append({"impact": gap, "tag": "sales", "title": f"Work on {WEEKDAY_NAMES[low]}s",
                            "detail": f"{WEEKDAY_NAMES[low]}s average {m(avgs[low])}, against {m(overall)} on a "
                                      f"typical trading day over the last 12 weeks. Shorter hours or an offer that "
                                      f"day could close part of the gap: a quarter of it is about {m(gap)} a month."})

    be = a.get("breakeven")
    if be:
        need = be["daily_sales_needed"] - be.get("projected_daily_sales_next_28d", be["actual_daily_sales_last_28d"])
        if need > 0:
            actions.append({"impact": need * 30, "tag": "sales", "title": "Lift daily sales above break-even",
                            "detail": f"The forecast expects {m(be.get('projected_daily_sales_next_28d', 0))} a day, "
                                      f"{m(need)} short of the {m(be['daily_sales_needed'])} break-even."})
    if tight and s["lowest_p50"]["balance"] < fc.buffer:
        actions.append({"impact": max(0.0, fc.buffer - s["lowest_p50"]["balance"]) / 2, "tag": "cash",
                        "title": "Hold back spending you can delay",
                        "detail": f"The expected low is {m(s['lowest_p50']['balance'])} around {s['lowest_p50']['date']}, "
                                  f"under your {m(fc.buffer)} buffer. Delay purchases that can wait until after then."})
    actions.sort(key=lambda x: (focus and x["tag"] != focus, -x["impact"]))
    for x in actions:
        x["impact"] = round(x["impact"], 2)
    return {"actions": actions[:6], "alert_level": s["alert_level"], "currency_symbol": ctx.symbol, "focus": focus}


# ----------------------------------------------------------------------------- one future day
def balance_on(ctx, day: date) -> dict:
    asof = ctx.ledger.as_of
    offset = (day - asof).days
    if offset < 1:
        return {"available": False, "reason": "That date is not in the future of your books.", "date": day.isoformat()}
    if offset > 90:
        return {"available": False, "date": day.isoformat(),
                "reason": "That is more than 90 days ahead. The forecast gets too uncertain to name a balance for one day."}
    fc = ctx.ensure_forecast()
    if offset > len(fc.days):
        fc = fc_mod.forecast(ctx.ledger, horizon=offset)
    row = fc.days[offset - 1]
    return {"available": True, "date": day.isoformat(), "p10": row["p10"], "p50": row["p50"], "p90": row["p90"],
            "prob_below_zero": row["prob_below_zero"], "buffer": fc.buffer, "currency_symbol": ctx.symbol}


# ----------------------------------------------------------------------------- unusual entries
INSTRUCTION_WORDS = ("ignore", "transfer", "system", "instruction", "urgent", "send", "override", "password", "pin")


def unusual_transactions(ctx, days: int = 90) -> dict:
    """Payments much bigger than usual for that payee, payees seen for the first time recently, and memo text
    that reads like an instruction. Memo text is reported, never followed."""
    asof = ctx.ledger.as_of
    start = asof - timedelta(days=days - 1)
    history = {}
    for t in ctx.ledger.txns:
        if t.amount < 0:
            history.setdefault(t.counterparty, []).append(-t.amount)
    first_seen = {}
    for t in ctx.ledger.txns:
        first_seen.setdefault(t.counterparty, t.date)
    found = []
    for t in ctx.ledger.between(start, asof):
        memo = (t.memo or "").lower()
        if any(w in memo for w in INSTRUCTION_WORDS):
            found.append({"date": t.date.isoformat(), "counterparty": t.counterparty, "amount": t.amount,
                          "why": "its memo reads like an instruction (possible scam or injection). I ignored it."})
            continue
        if t.amount < 0:
            vals = sorted(history[t.counterparty])
            med = vals[len(vals) // 2]
            if len(vals) >= 4 and -t.amount > 2.5 * med and t.category not in ("tax", "rent"):
                found.append({"date": t.date.isoformat(), "counterparty": t.counterparty, "amount": t.amount,
                              "why": f"it is {(-t.amount) / med:.1f} times the usual payment to them"})
            elif first_seen[t.counterparty] >= asof - timedelta(days=30) and t.category != "sales":
                found.append({"date": t.date.isoformat(), "counterparty": t.counterparty, "amount": t.amount,
                              "why": "a new payee in the last 30 days"})
    return {"days": days, "found": found[-8:], "checked": len(ctx.ledger.between(start, asof)),
            "currency_symbol": ctx.symbol}


# ----------------------------------------------------------------------------- what if
def what_if(ctx, amount: float = 0.0, monthly: float = 0.0, sales_pct: float = 0.0, label: str = "",
            start: date = None) -> dict:
    """Re-run the 60-day forecast with a one-off cost, a monthly cost or saving, or a change in sales.
    A positive amount is money out. A negative amount is a saving."""
    before = ctx.ensure_forecast()
    asof = ctx.ledger.as_of
    start = start or asof + timedelta(days=1)
    horizon = len(before.days)
    extra = []
    if amount:
        extra.append({"date": start.isoformat(), "label": label or "one-off", "amount": float(amount)})
    if monthly:
        k = 0
        while (start + timedelta(days=30 * k) - asof).days <= horizon:
            extra.append({"date": (start + timedelta(days=30 * k)).isoformat(), "label": label or "monthly",
                          "amount": float(monthly)})
            k += 1
    daily_sales = before.summary["projected_sales_next_28d"] / 28
    if sales_pct:
        change = daily_sales * sales_pct / 100
        for i in range(1, horizon + 1):
            extra.append({"date": (asof + timedelta(days=i)).isoformat(), "label": "sales change",
                          "amount": -change})
    after = fc_mod.forecast(ctx.ledger, horizon=horizon, commitments=list(ctx.ledger.commitments) + extra)
    b, a = before.summary, after.summary
    fcf = ctx.business.monthly_free_cash_flow
    if a["lowest_p50"]["balance"] >= before.buffer and a["probability_negative_any_day"] <= 0.10:
        verdict = "affordable"
    elif a["lowest_p50"]["balance"] >= 0 and a["probability_negative_any_day"] <= 0.35:
        verdict = "tight"
    else:
        verdict = "not now"
    headroom = None
    if amount and not monthly and not sales_pct:
        headroom = round(max(0.0, b["lowest_p50"]["balance"] - before.buffer), 2)
    pick = lambda s: {"lowest_p50": s["lowest_p50"], "lowest_p10": s["lowest_p10"],
                      "probability_negative_any_day": s["probability_negative_any_day"],
                      "first_risk_date": s["first_risk_date"], "alert_level": s["alert_level"]}
    return {"label": label, "amount": amount, "monthly": monthly, "sales_pct": sales_pct,
            "start": start.isoformat(), "days": horizon, "before": pick(b), "after": pick(a),
            "verdict": verdict, "headroom": headroom, "buffer": before.buffer,
            "free_cash_flow": round(fcf, 2),
            "monthly_share_of_free_cash_flow": round(monthly / fcf, 3) if monthly and fcf > 0 else None,
            "sales_change_per_month": round(daily_sales * sales_pct / 100 * 30, 2) if sales_pct else None,
            "currency_symbol": ctx.symbol}
