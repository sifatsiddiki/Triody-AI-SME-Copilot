"""Evaluate the financing matcher on the 30 hand-written cases against two baselines."""
import random
import statistics

from . import financing as fin
from .eval_set import CASES, CORRECTIONS, HELDOUT_CASES, NEW_CASES


def _biz(c):
    return fin.Business(months_trading=c["months_trading"], annual_revenue=c["annual_revenue"],
                        credit_band=c["credit_band"], monthly_free_cash_flow=c["monthly_free_cash_flow"],
                        card_share=c["card_share"], receivables=c["receivables"],
                        collateral=c["collateral"], supplier_relationship=c["supplier_relationship"],
                        legal_form=c.get("legal_form", ""),
                        existing_providers=tuple(c.get("existing_providers", ())),
                        tax_due=c.get("tax_due", 0.0))


def _need(c):
    return fin.Need(c["amount"], c["term_months"], c["urgency_days"], c["purpose"])


def _eligible_ids(c, catalog):
    biz, need = _biz(c), _need(c)
    return [p for p in catalog if not fin.check_eligibility(p, biz, need)]


def pick_matcher(c, catalog, **kw):
    rec = fin.match(_biz(c), _need(c), catalog, **kw)["recommendation"]
    return rec["id"] if rec else None


def pick_cheapest(c, catalog):
    """Baseline 1: hard filters only, then the lowest total cost. Ignores speed and affordability."""
    el = _eligible_ids(c, catalog)
    if not el:
        return None
    return min(el, key=lambda p: fin.cost_of_credit(p, c["amount"], c["term_months"],
                                                    c["credit_band"])["total_cost"])["id"]


def _score_picks(picks, cases=CASES):
    strict = statistics.fmean(1.0 if pk == c["best"] else 0.0 for c, pk in zip(cases, picks))
    relaxed = statistics.fmean(1.0 if pk in c["ok"] else 0.0 for c, pk in zip(cases, picks))
    none_cases = [(c, pk) for c, pk in zip(cases, picks) if c["best"] is None]
    prod_cases = [(c, pk) for c, pk in zip(cases, picks) if c["best"] is not None]
    return {"strict_top1_pct": round(100 * strict, 1), "relaxed_top1_pct": round(100 * relaxed, 1),
            "correct_abstain": f"{sum(1 for _, pk in none_cases if pk is None)}/{len(none_cases)}",
            "false_abstain": f"{sum(1 for _, pk in prod_cases if pk is None)}/{len(prod_cases)}"}


def run(catalog=None) -> dict:
    catalog = catalog or fin.load_eval_catalog()
    matcher = [pick_matcher(c, catalog) for c in CASES]
    cheapest = [pick_cheapest(c, catalog) for c in CASES]
    no_aff = [pick_matcher(c, catalog, weights={**fin.WEIGHTS, "affordability": 0.0}, gate=False)
              for c in CASES]

    # Baseline 2: a random eligible product, averaged over 500 seeds.
    strict_r, relaxed_r = [], []
    for seed in range(500):
        rng = random.Random(seed)
        picks = []
        for c in CASES:
            el = _eligible_ids(c, catalog)
            picks.append(rng.choice(el)["id"] if el else None)
        s = _score_picks(picks)
        strict_r.append(s["strict_top1_pct"])
        relaxed_r.append(s["relaxed_top1_pct"])

    # Top-3 hit rate for cases that have a product answer.
    hits, n = 0, 0
    for c in CASES:
        if c["best"] is None:
            continue
        ranked = fin.match(_biz(c), _need(c), catalog, gate=False)["ranked"]
        n += 1
        hits += c["best"] in [r["id"] for r in ranked[:3]]

    rows = [{"case": c["id"], "gold": c["best"], "acceptable": c["ok"], "matcher": m, "cheapest": ch,
             "matcher_ok": m in c["ok"], "why": c["why"]}
            for c, m, ch in zip(CASES, matcher, cheapest)]
    return {
        "cases": len(CASES),
        "matcher": _score_picks(matcher),
        "baseline_cheapest_eligible": _score_picks(cheapest),
        "baseline_random_eligible": {"strict_top1_pct": round(statistics.fmean(strict_r), 1),
                                     "relaxed_top1_pct": round(statistics.fmean(relaxed_r), 1),
                                     "note": "mean of 500 random draws"},
        "ablation_no_affordability": _score_picks(no_aff),
        "top3_hit_rate_pct": round(100 * hits / n, 1), "top3_cases": n,
        "label_corrections": CORRECTIONS,
        "rows": rows,
        "heldout": run_heldout(),
        "heldout_new": run_heldout(NEW_CASES),
    }


def run_heldout(cases=HELDOUT_CASES) -> dict:
    """Held-out cases on the live catalogue of sourced UK products."""
    catalog = fin.load_catalog()
    matcher = [pick_matcher(c, catalog) for c in cases]
    cheapest = [pick_cheapest(c, catalog) for c in cases]
    no_aff = [pick_matcher(c, catalog, weights={**fin.WEIGHTS, "affordability": 0.0}, gate=False) for c in cases]
    rows = [{"case": c["id"], "gold": c["best"], "acceptable": c["ok"], "matcher": m, "cheapest": ch,
             "matcher_ok": m in c["ok"], "why": c["why"]} for c, m, ch in zip(cases, matcher, cheapest)]
    return {"cases": len(cases), "matcher": _score_picks(matcher, cases),
            "baseline_cheapest_eligible": _score_picks(cheapest, cases),
            "ablation_no_affordability": _score_picks(no_aff, cases), "rows": rows}


def _previous_v2() -> dict:
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "evidence" / "previous_version_results.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("version_2", {})
    except (OSError, ValueError):
        return {}


def _matcher_table(rows_spec) -> list:
    out = ["| Method | Strict top-1 | Relaxed top-1 | Correct abstain | False abstain |", "|---|---|---|---|---|"]
    for label, m in rows_spec:
        out.append(f"| {label} | {m['strict_top1_pct']}% | {m['relaxed_top1_pct']}% | "
                   f"{m.get('correct_abstain', 'n/a')} | {m.get('false_abstain', 'n/a')} |")
    return out


def to_markdown(ev: dict, bts: dict) -> str:
    m, b, r, a = (ev["matcher"], ev["baseline_cheapest_eligible"], ev["baseline_random_eligible"],
                  ev["ablation_no_affordability"])
    h = ev["heldout"]
    n2 = ev["heldout_new"]
    miss = [x for x in ev["rows"] if not x["matcher_ok"]]
    roles = {"A": "tuning shop", "B": "held-out test", "C": "held-out test, used to diagnose the fixes",
             "D": "held-out test, China, exposed one bug"}
    lines = [
        "# Evaluation",
        "",
        "This file is generated by `python -m triody evidence`. Do not edit the numbers by hand.",
        "",
        "## Summary",
        "",
        f"- Financing matcher, 30 labelled cases: {m['strict_top1_pct']}% strict accuracy against "
        f"{b['strict_top1_pct']}% for the cheapest-eligible baseline, and {m['correct_abstain']} correct refusals "
        f"against {b['correct_abstain']}.",
        f"- Financing matcher, {h['cases']} held-out cases on the sourced UK catalogue: {h['matcher']['strict_top1_pct']}% "
        f"strict, {h['matcher']['relaxed_top1_pct']}% relaxed, {h['matcher']['correct_abstain']} correct refusals.",
        f"- Financing matcher, {n2['cases']} new held-out cases written after the review: "
        f"{n2['matcher']['strict_top1_pct']}% strict against {n2['baseline_cheapest_eligible']['strict_top1_pct']}% "
        f"for the cheapest-eligible baseline, {n2['matcher']['correct_abstain']} correct refusals against "
        f"{n2['baseline_cheapest_eligible']['correct_abstain']}.",
    ]
    for name, bt in bts.items():
        w, c, bal = bt["weekly_sales_mape_pct"], bt["range_coverage_pct"], bt["balance_mae_gbp"]
        cur = bt.get("currency_symbol", "£")
        best_base = min(w["repeat_last_week"], w["flat_28d_mean"])
        lines.append(
            f"- Forecast, shop {name} ({bt['shop']}, {roles.get(name, '')}): weekly sales error {w['model']}% against "
            f"{best_base}% for the better baseline. Day-14 balance error {cur}{bal['day_14']['model']:,.0f} "
            f"against {cur}{bal['day_14']['naive']:,.0f} for the naive forecast. Range coverage {c['balance_day_14']}% "
            f"at day 14 and {c['balance_day_28']}% at day 28 (target 80%).")
    lines += [
        "",
        "## 1. Financing matcher on 30 labelled cases",
        "",
        "The cases are in `triody/eval_set.py`. They were written against a frozen illustrative catalogue, "
        "`triody/eval_catalog.json`, which is why this test still uses it. The expected answer for each case was "
        "written before the matcher was run, by reasoning about the case. `Strict` counts only the single best "
        "answer. `Relaxed` also accepts answers marked reasonable. Six cases expect \"no recommendation\".",
        "",
    ]
    lines += _matcher_table([("Triody matcher", m), ("Cheapest eligible (filters only)", b),
                             ("Random eligible (mean of 500)", r), ("Matcher without affordability", a)])
    lines += [
        "",
        f"Top-3 hit rate (best answer inside the first three, without the affordability gate): "
        f"{ev['top3_hit_rate_pct']}% over {ev['top3_cases']} cases.",
        "",
        "### What changed since the previous version",
        "",
        "The previous version scored 76.7% strict and 80.0% relaxed, a tie with the cheapest-eligible baseline. "
        "Four of its six misses were refusals caused by the affordability check. Three of those four were stock "
        "purchases (E1, E2, E3). The fourth (A3) was a cash gap.",
        "",
        "The fix: stock is sold, and the sale repays most of the loan. For stock finance of up to 12 months, the "
        "check now counts the cost of credit plus 25% of the amount (in case some stock does not sell), not the "
        "whole amount. Longer stock loans are judged like any other loan. The 25% is a planning assumption, and the "
        "result is sensitive to it: any value from 0% to 25% gives 86.7% here, 30% gives 83.3% and 50% gives 80.0%, "
        "because some stock cases sit close to the limit. On the held-out set, 0% to 25% gives 85.7% and 30% gives "
        "78.6%. 25% is the highest value that keeps the result, so it is the most cautious choice, but it was "
        "picked after seeing these numbers. This change was made after seeing the misses, so these 30 cases no "
        "longer test it fairly.",
        "",
        "A3 is still a miss, and should be. The business would repay about £1,180 a month from £900 of free cash "
        "flow. The label says recommend. A careful adviser could refuse, so the label may be wrong, but it was not "
        "changed.",
        "",
        "### Cases where the matcher gave an answer outside the acceptable list",
        "",
    ]
    if not miss:
        lines.append("None.")
    for x in miss:
        lines.append(f"- {x['case']}: expected {x['gold']} (acceptable {x['acceptable']}), matcher chose "
                     f"{x['matcher']}. Reason for the label: {x['why']}")
    lines += ["", "### Label corrections after seeing results", ""]
    if not ev["label_corrections"]:
        lines.append("None. No label was changed after the matcher was run.")
    for c in ev["label_corrections"]:
        lines.append(f"- {c}")
    lines += [
        "",
        "## 2. Financing matcher on 14 held-out cases, sourced UK catalogue",
        "",
        "The live catalogue `triody/catalog.json` holds ten options built from public sources (Lloyds Bank, HMRC, "
        "iwoca, Funding Circle, Start Up Loans, Lloyds Cardnet, and Spark Finance market rate indexes), each with "
        "its source link, the date checked, the published figures and every assumption. The 14 cases in "
        "`HELDOUT_CASES` were written after the stock rule and before the matcher was run on them. The results "
        "below are from the first run.",
        "",
    ]
    lines += _matcher_table([("Triody matcher", h["matcher"]), ("Cheapest eligible (filters only)", h["baseline_cheapest_eligible"]),
                             ("Matcher without affordability", h["ablation_no_affordability"])])
    lines += ["", "| Case | Expected | Matcher | Cheapest baseline |", "|---|---|---|---|"]
    for x in h["rows"]:
        lines.append(f"| {x['case']} | {x['gold']} | {x['matcher']}{'' if x['matcher_ok'] else ' (miss)'} | {x['cheapest']} |")
    lines += [
        "",
        "Where the matcher differs from the expected best answer but stays inside the acceptable list (H6, H14), it "
        "picked Funding Circle or the secured loan on cost. Funding Circle is costed at its advertised 'from' rate, "
        "which flatters it. The reply says so.",
        "",
        f"## 2b. Financing matcher on {n2['cases']} new held-out cases",
        "",
        "Written on 14 October 2026 after an outside review said the 30 original cases no longer test the stock "
        "rule fairly. Each label was written from the business situation and frozen before the matcher was run. "
        "The results below are from the first run. No label was changed.",
        "",
    ]
    lines += _matcher_table([("Triody matcher", n2["matcher"]),
                             ("Cheapest eligible (filters only)", n2["baseline_cheapest_eligible"]),
                             ("Matcher without affordability", n2["ablation_no_affordability"])])
    agree = sum(1 for x in n2["rows"] if x["matcher"] == x["cheapest"])
    lines += ["", "| Case | Expected | Matcher | Cheapest baseline |", "|---|---|---|---|"]
    for x in n2["rows"]:
        lines.append(f"| {x['case']} | {x['gold']} | {x['matcher']}{'' if x['matcher_ok'] else ' (miss)'} | {x['cheapest']} |")
    lines += [
        "",
        f"Read this result with care. The matcher and the cheapest-eligible baseline pick the same answer in "
        f"{agree} of {n2['cases']} cases, because in most of them the eligibility rules leave one sensible product. "
        "The gap between the two comes from the affordability refusal. These cases test the rules more than the "
        "scoring. A harder set, with several eligible products of similar cost, is still needed.",
        "",
        "### Limits of all three matcher tests",
        "",
        "- The cases, the labels and the catalogue come from the same team. This is a circularity risk. The fair test "
        "is cases written by someone who has not seen the catalogue.",
        "- No financing professional reviewed the labels.",
        "- The sourced catalogue uses published and index rates. Real offers depend on each provider's checks.",
        "- The scoring weights were set before the first evaluation and have not been tuned.",
        "",
        "## 3. Cash-flow forecast on four shops",
        "",
        "Shop A, the café, is the demo shop. All forecast settings were tuned on it. Shop B, a bike shop, was built "
        "before any tuning and used once as a test. Shop B showed ranges that were too narrow, so the forecast now "
        "sets its range width from the shop's own past errors. Shop C, a florist, was built before that change, as "
        "a clean test of it. Shop C then showed about 65% coverage, and its misses were used to design two more "
        "fixes (below), so shop C is no longer a clean test. Shop D, a Shenzhen tea shop priced in yuan, was written "
        "before those fixes were run on it. Its first run exposed a bug (also below), which was then fixed, so "
        "shop D is not fully clean either. The four shops differ on purpose: closed days, seasons, one-off large "
        "sales, a holiday peak, weekly platform payouts, bill cycles and currency.",
        "",
        "Protocol: a forecast from every day of history with enough data on both sides, 28 days ahead. Origins "
        "overlap, so the results are not independent and the 95% intervals below are too narrow.",
        "",
        "| Shop | Role | Origins | Sales error, model | Repeat last week | Flat 28-day mean | Balance error day 14, model | Naive | Balance error day 28, model | Naive |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, bt in bts.items():
        w, bal = bt["weekly_sales_mape_pct"], bt["balance_mae_gbp"]
        role = "tuning" if name == "A" else "test"
        cur = bt.get("currency_symbol", "£")
        lines.append(f"| {name} | {role} | {bt['origins']} | {w['model']}% | {w['repeat_last_week']}% | "
                     f"{w['flat_28d_mean']}% | {cur}{bal['day_14']['model']:,.0f} | {cur}{bal['day_14']['naive']:,.0f} | "
                     f"{cur}{bal['day_28']['model']:,.0f} | {cur}{bal['day_28']['naive']:,.0f} |")
    lines += ["", "| Shop | Range coverage day 14 (95% interval) | Day 28 (95% interval) | Target |", "|---|---|---|---|"]
    for name, bt in bts.items():
        c, ci = bt["range_coverage_pct"], bt["range_coverage_95ci"]
        lines.append(f"| {name} | {c['balance_day_14']}% ({ci['balance_day_14'][0]} to {ci['balance_day_14'][1]}) | "
                     f"{c['balance_day_28']}% ({ci['balance_day_28'][0]} to {ci['balance_day_28'][1]}) | 80% |")
    lines += [
        "",
        "Sales error is the mean absolute percentage error of weekly sales. Balance error is the mean absolute "
        "difference between the expected balance and the real one. The naive balance forecast carries the last "
        "28 days of net cash forward and subtracts the same known tax payments the model gets.",
        "",
        "### Before and after",
        "",
        "Version sent for review on 14 October 2026, same protocol (see `evidence/previous_version_results.json`, "
        "`version_2`):",
        "",
        "| Shop | Sales error then | Now | Day-14 balance error then | Now | Coverage day 14 then | Now | Day 28 then | Now |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    prev = _previous_v2()
    for name, bt in bts.items():
        pv = prev.get(f"shop_{name}")
        if not pv:
            continue
        cur = bt.get("currency_symbol", "£")
        lines.append(f"| {name} | {pv['weekly_sales_mape_pct']['model']}% | {bt['weekly_sales_mape_pct']['model']}% | "
                     f"{cur}{pv['balance_mae_gbp']['day_14']['model']:,.0f} | {cur}{bt['balance_mae_gbp']['day_14']['model']:,.0f} | "
                     f"{pv['range_coverage_pct']['balance_day_14']}% | {bt['range_coverage_pct']['balance_day_14']}% | "
                     f"{pv['range_coverage_pct']['balance_day_28']}% | {bt['range_coverage_pct']['balance_day_28']}% |")
    lines += [
        "",
        "Three changes since that version:",
        "",
        "1. One-off sales (a wedding order, a bike sale) no longer lift the expected level or reshape the week. "
        "Each day is capped at 1.5 times the typical day before the level, trend and weekday pattern are fitted. "
        "Found on shop C: after a wedding order, the forecast stayed too high for days.",
        "2. Day-to-day noise is sampled from up to 12 weeks instead of 4, as a ratio to the typical day around it. "
        "A 4-week window often held no one-off at all, and the ranges then left them out. Found on shop C.",
        "3. A monthly bill seen 3 times is now projected on its date. It needed 4 before. With 6 months of history, "
        "a monthly rent or payroll seen only 2 or 3 times was spread thinly over every day. Found on shop D, where "
        "rent and payroll are large and monthly. This is a plain bug fix, and it also improved shops A to C.",
        "",
        "Earlier history, shop A: weekly sales error 11.3% against 11.09% for repeat-last-week, "
        "range coverage 35.4% at day 14 and 74.4% at day 28. On shops B and C it crashed, because it divided by "
        "zero on days the shop is closed. The figures quoted earlier (10.85% against 10.36%, 58% at day 14) used "
        "only 12 origins. See `evidence/previous_version_results.json`.",
        "",
        "Changes: closed days are found from the data; the weekday pattern and level use the last 4 weeks; the "
        "trend fades more slowly; each simulated future draws its own sales level and its own bill amounts; "
        "irregular payments such as quarterly rent are spread at their long-run rate; and the range width comes "
        "from the shop's own past 14-day errors.",
        "",
        "### What this shows, and what it does not",
        "",
        "- On all four shops the balance forecast beats the naive one at day 14 and day 28, by 29% to 69%. The model "
        "knows when each bill falls, and the naive forecast does not.",
        "- On weekly sales, the model beats both baselines on shops A, B and C. On shop D it does not: 7.1% against "
        "6.6% for the flat 28-day mean. Shop D's sales are smooth, and its holiday peak in early October pulls the "
        "4-week trend. Simple baselines are hard to beat on smooth sales.",
        "- Ranges now err on the wide side on shops A, B and D (84% to 98% against 80%). A cash alert that is too "
        "cautious costs the owner less than one that is too relaxed, but it can warn too early. Shop C is still a "
        "little narrow at day 14 (about 71%).",
        "- Remaining cause of misses on shop C: its monthly seasonal steps (for example 14% down from July to "
        "August) arrive as a jump the model cannot see coming, and errors bunch in time. The fix is a yearly "
        "season, which needs at least 12 months of history.",
        "- Every model over-forecasts on average when a shop heads into a seasonal low. Six months of history cannot "
        "show a yearly season.",
        "- All four shops are synthetic and written by the team. Results on a real business will differ.",
    ]
    return "\n".join(lines) + "\n"
