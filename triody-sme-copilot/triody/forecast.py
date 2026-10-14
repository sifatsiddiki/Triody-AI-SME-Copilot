"""Cash-flow forecast.

Three parts:
1. A sales model: weekday pattern plus a damped trend, fitted on the last 4 weeks. Days on which
   the shop is closed are found from the data and kept at zero.
2. Outflows: recurring payments found in the ledger (weekly, every two weeks, monthly)
   plus commitments the owner states, such as a VAT bill. Payments that do not repeat on a
   fixed cycle (for example quarterly rent) are spread over the days at their long-run rate.
3. Monte Carlo: 2000 simulated futures give a range (p10, p50, p90) for every day. Each future
   draws its own sales level, trend, daily noise and bill amounts from what the history shows.

Everything uses only data up to the forecast date, so the backtest is honest.
"""
import calendar
import math
import random
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import config

WINDOW_DAYS = 28                    # weekday pattern, level and trend come from the last 4 weeks
LEVEL_HALF_LIFE_DAYS = 7            # recent days count more when estimating today's sales level
DAMPING_PER_WEEK = 0.6              # how much of the trend survives each week
STRUCTURAL_SIGMA_PER_MONTH = 0.05   # extra uncertainty for seasonal change the model cannot see
LEVEL_SIGMA = 0.14                  # uncertainty of today's sales level itself
CLOSED_DAY_SHARE = 0.05             # a weekday selling under 5% of the average is a closed day
# The values above were chosen on the backtest of shop A (the café) only. Shop B (the bike
# shop) was built before tuning and is used only to test them. See docs/EVALUATION.md.

CALIBRATION_HORIZON_DAYS = 14       # self-calibration: how far ahead past errors are measured
CALIBRATION_LOOKBACK_DAYS = 90      # and over how many past days
CALIBRATION_MIN_ORIGINS = 10        # below this many past forecasts, LEVEL_SIGMA is used as it is
# Self-calibration was added after the shop B result showed ranges that were too narrow for a
# lumpier business. Shop C (the florist) was built before this change, as a clean test of it.

ONE_OFF_CAP = 1.5                   # a day selling more than 1.5 times the typical day is treated as a one-off
RESIDUAL_WINDOW_DAYS = 84           # day-to-day noise, including one-offs, is sampled from up to 12 weeks
LOCAL_LEVEL_HALF_WINDOW = 13        # the typical day around each past day is taken from +/- 13 days
# The two settings above were added after shop C still showed about 65% coverage. Looking at its
# misses showed two causes: one-off orders (weddings) pushed the level up for days after they
# happened, and a 4-week window often held no one-off at all, so the ranges then left them out.
# Shop D (the Shenzhen tea shop) was written before this change was run on it, as a clean test.


# ----------------------------------------------------------------------------- sales model
@dataclass
class SalesModel:
    weekday_index: list
    level: float          # deseasonalised daily sales today (recent days weigh more)
    slope: float          # change per day in deseasonalised sales
    slope_se: float
    residuals: list       # multiplicative noise around the typical day, one-offs included
    lump_factor: float = 1.0   # mean of the residuals: how much one-offs add on average (reported only)

    def damped_steps(self, i: int) -> float:
        phi = DAMPING_PER_WEEK ** (1 / 7)
        return phi * (1 - phi ** i) / (1 - phi)

    def level_at(self, i: int, slope=None) -> float:
        s = self.slope if slope is None else slope
        return max(0.3 * self.level, self.level + s * self.damped_steps(i))

    def point(self, asof: date, i: int) -> float:
        d = asof + timedelta(days=i)
        return self.level_at(i) * self.weekday_index[d.weekday()]


def _clip_one_offs(values: list) -> list:
    """Cap each value at ONE_OFF_CAP times the median, so a single big order does not move the level."""
    if not values:
        return values
    cap = ONE_OFF_CAP * statistics.median(values)
    return [min(v, cap) for v in values]


def _residual_pool(ledger, asof: date, idx: list) -> list:
    """Each past open day's sales as a ratio to the typical day around it (one-offs capped).

    Up to RESIDUAL_WINDOW_DAYS of history are used, so rare one-off orders show up in the pool at
    about the rate they really happen. Only data up to `asof` is used."""
    first = min((t.date for t in ledger.txns), default=asof)
    start = max(first, asof - timedelta(days=RESIDUAL_WINDOW_DAYS - 1))
    sales = ledger.daily(start, asof, "sales")
    days = [d for d in sorted(sales) if idx[d.weekday()] > 0]
    deseason = {d: sales[d] / idx[d.weekday()] for d in days}
    pool = []
    for d in days:
        near = [deseason[x] for x in days if abs((x - d).days) <= LOCAL_LEVEL_HALF_WINDOW]
        typical = statistics.fmean(_clip_one_offs(near))
        if typical > 0:
            pool.append(deseason[d] / typical)
    return pool or [1.0]


def fit_sales_model(ledger, asof: date) -> SalesModel:
    start = asof - timedelta(days=WINDOW_DAYS - 1)
    sales = ledger.daily(start, asof, "sales")
    days = sorted(sales)
    overall = statistics.fmean(sales.values())
    if overall <= 0:
        return SalesModel([0.0] * 7, 0.0, 0.0, 0.0, [1.0])
    by_wd = {w: [] for w in range(7)}
    for d in days:
        by_wd[d.weekday()].append(sales[d])
    by_wd = {w: _clip_one_offs(v) for w, v in by_wd.items()}   # one big order must not reshape the week
    overall = statistics.fmean(x for v in by_wd.values() for x in v)
    idx = [statistics.fmean(by_wd[w]) / overall for w in range(7)]
    idx = [i if i >= CLOSED_DAY_SHARE else 0.0 for i in idx]
    mean_idx = statistics.fmean(idx)
    idx = [i / mean_idx for i in idx]

    open_days = [d for d in days if idx[d.weekday()] > 0]
    deseason = _clip_one_offs([sales[d] / idx[d.weekday()] for d in open_days])
    t = [(d - days[0]).days for d in open_days]
    n = len(deseason)
    t_bar, y_bar = statistics.fmean(t), statistics.fmean(deseason)
    sxx = sum((x - t_bar) ** 2 for x in t)
    slope = sum((x - t_bar) * (y - y_bar) for x, y in zip(t, deseason)) / sxx
    intercept = y_bar - slope * t_bar
    fitted = [max(1e-6, intercept + slope * x) for x in t]
    resid = [y - f for y, f in zip(deseason, fitted)]
    s2 = sum(r * r for r in resid) / max(1, n - 2)
    slope_se = math.sqrt(s2 / sxx)
    ratios = _residual_pool(ledger, asof, idx)
    lump = statistics.fmean(ratios)
    level, prev_t = deseason[0], t[0]
    for y, x in zip(deseason[1:], t[1:]):
        alpha = 1 - 0.5 ** ((x - prev_t) / LEVEL_HALF_LIFE_DAYS)
        level = alpha * y + (1 - alpha) * level
        prev_t = x
    return SalesModel(idx, level, slope, slope_se, ratios, lump)


def calibrated_level_sigma(ledger, asof: date) -> dict:
    """Measure how wrong the sales model has been on this shop's own history.

    For every past day with enough data on both sides, fit the model as of that day, forecast the
    next 14 days of sales and compare with what happened. The spread of those errors (p10 to p90,
    turned into a standard deviation) becomes the level uncertainty, never lower than LEVEL_SIGMA.
    Only data up to `asof` is used.
    """
    first = min((t.date for t in ledger.txns), default=asof)
    h = CALIBRATION_HORIZON_DAYS
    o = max(first + timedelta(days=WINDOW_DAYS - 1), asof - timedelta(days=CALIBRATION_LOOKBACK_DAYS))
    errs = []
    while o + timedelta(days=h) <= asof:
        m = fit_sales_model(ledger, o)
        pred = sum(m.point(o, i) for i in range(1, h + 1))
        act = sum(ledger.daily(o + timedelta(days=1), o + timedelta(days=h), "sales").values())
        if pred > 0:
            errs.append(act / pred - 1)
        o += timedelta(days=1)
    if len(errs) < CALIBRATION_MIN_ORIGINS:
        return {"sigma": LEVEL_SIGMA, "past_forecasts": len(errs), "measured": None}
    errs.sort()
    measured = (_q(errs, 0.9) - _q(errs, 0.1)) / 2.563
    return {"sigma": max(LEVEL_SIGMA, measured), "past_forecasts": len(errs), "measured": round(measured, 4)}


# ----------------------------------------------------------------------------- recurring payments
MIN_REPEATS = 3     # a payment seen 3 times on a steady cycle is projected forward. It was 4 until
                    # shop D showed that, with 6 months of history, a monthly rent or payroll seen only
                    # 2 or 3 times was spread thinly over every day instead of falling on its date.
@dataclass
class Recurring:
    counterparty: str
    category: str
    cadence: str
    amount: float
    dates: list
    ratio: float = None     # supplier bill as a share of the previous 7 days of sales
    history: list = field(default_factory=list)   # recent amounts, used to vary bills in the simulation
    ratios: list = field(default_factory=list)    # recent sales shares for bills linked to sales

    def describe(self) -> dict:
        return {"counterparty": self.counterparty, "category": self.category,
                "cadence": self.cadence, "amount": round(self.amount, 2),
                "next_dates": [d.isoformat() for d in self.dates[:3]],
                "linked_to_sales": self.ratio is not None}


def _cadence(gaps):
    med = statistics.median(gaps)
    for name, lo, hi, step in (("weekly", 6, 8, 7), ("every two weeks", 13, 15, 14),
                               ("monthly", 27, 33, 0)):
        if lo <= med <= hi and sum(1 for g in gaps if abs(g - med) <= 3) / len(gaps) >= 0.7:
            return name, step
    return None, None


def _add_month(d: date, day: int) -> date:
    y, m = (d.year + (d.month == 12), d.month % 12 + 1)
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def detect_recurring(ledger, asof: date, horizon: int):
    """Find payments that repeat, and project their next dates inside the horizon."""
    groups = {}
    for t in ledger.txns:
        if t.date <= asof and t.amount < 0 and t.category not in ("tax",):
            groups.setdefault((t.counterparty, t.category), []).append(t)
    recurring, irregular_total = [], 0.0
    first_day = min((t.date for t in ledger.txns), default=asof)
    lookback = max(28, (asof - first_day).days + 1)
    window_start = asof - timedelta(days=lookback - 1)
    end = asof + timedelta(days=horizon)
    for (who, cat), items in groups.items():
        items.sort(key=lambda t: t.date)
        cadence = None
        if len(items) >= MIN_REPEATS:
            gaps = [(b.date - a.date).days for a, b in zip(items, items[1:])][-8:]
            cadence, step = _cadence(gaps)
        if cadence is None:
            irregular_total += sum(-t.amount for t in items if t.date >= window_start)
            continue
        amount = statistics.fmean(-t.amount for t in items[-3:])
        ratio, vals = None, []
        if cat == "supplier" and cadence == "weekly":
            for t in items[-6:]:
                prev = sum(ledger.daily(t.date - timedelta(days=7), t.date - timedelta(days=1), "sales").values())
                if prev > 0:
                    vals.append(-t.amount / prev)
            ratio = statistics.fmean(vals[-4:]) if vals else None
        dates, d = [], items[-1].date
        while True:
            d = _add_month(d, items[-1].date.day) if cadence == "monthly" else d + timedelta(days=step)
            if d > end:
                break
            if d > asof:
                dates.append(d)
        history = [-t.amount for t in items[-6:]]
        recurring.append(Recurring(who, cat, cadence, amount, dates, ratio, history, vals))
    return recurring, irregular_total / lookback


# ----------------------------------------------------------------------------- forecast
@dataclass
class Forecast:
    asof: date
    opening_balance: float
    buffer: float
    days: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    schedule: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"asof": self.asof.isoformat(), "opening_balance": round(self.opening_balance, 2),
                "buffer": self.buffer, "days": self.days, "summary": self.summary,
                "schedule": self.schedule, "assumptions": self.assumptions}


def _q(sorted_vals, q):
    return sorted_vals[int(q * (len(sorted_vals) - 1))]


def forecast(ledger, asof: date = None, horizon: int = config.HORIZON_DAYS,
             simulations: int = config.SIMULATIONS, seed: int = config.SEED,
             buffer: float = None, commitments=None) -> Forecast:
    asof = asof or ledger.as_of
    if buffer is None:
        buffer = float(ledger.profile.get("safety_buffer", config.ALERT_BUFFER_GBP))
    commitments = ledger.commitments if commitments is None else commitments
    model = fit_sales_model(ledger, asof)
    calibration = calibrated_level_sigma(ledger, asof)
    level_sigma = calibration["sigma"]
    recurring, other_daily = detect_recurring(ledger, asof, horizon)
    start_balance = ledger.balance_on(asof)

    fixed = [0.0] * (horizon + 1)           # owner commitments and the long-run rate of irregular spend
    events = {}                             # day offset -> recurring payments due that day
    for r in recurring:
        for d in r.dates:
            i = (d - asof).days
            if 1 <= i <= horizon:
                events.setdefault(i, []).append(r)
    schedule = [r.describe() for r in recurring]
    for c in commitments:
        d = date.fromisoformat(c["date"])
        i = (d - asof).days
        if 1 <= i <= horizon:
            fixed[i] += float(c["amount"])
            schedule.append({"counterparty": c["label"], "category": "commitment (owner)",
                             "cadence": "one-off", "amount": float(c["amount"]),
                             "next_dates": [d.isoformat()], "linked_to_sales": False})
    for i in range(1, horizon + 1):
        fixed[i] += other_daily

    hist = ledger.daily(asof - timedelta(days=6), asof, "sales")
    history_sales = [hist[asof - timedelta(days=6 - k)] for k in range(7)]

    rng = random.Random(seed)
    per_day = [[] for _ in range(horizon + 1)]
    min_paths = []
    for _ in range(simulations):
        slope = model.slope + rng.gauss(0, model.slope_se)
        drift = rng.gauss(0, STRUCTURAL_SIGMA_PER_MONTH)
        shock = rng.gauss(0, level_sigma)
        sales = list(history_sales)
        bal, lowest = start_balance, start_balance
        for i in range(1, horizon + 1):
            d = asof + timedelta(days=i)
            lvl = model.level_at(i, slope) * max(0.3, 1 + shock + drift * i / 30)
            s = lvl * model.weekday_index[d.weekday()] * rng.choice(model.residuals)
            sales.append(s)
            out = fixed[i]
            for r in events.get(i, ()):
                if r.ratio is not None:
                    out += rng.choice(r.ratios) * sum(sales[-8:-1])
                else:
                    out += rng.choice(r.history)
            bal += s - out
            per_day[i].append(bal)
            lowest = min(lowest, bal)
        min_paths.append(lowest)

    days = []
    for i in range(1, horizon + 1):
        v = sorted(per_day[i])
        days.append({"date": (asof + timedelta(days=i)).isoformat(),
                     "p10": round(_q(v, 0.10), 2), "p50": round(_q(v, 0.50), 2),
                     "p90": round(_q(v, 0.90), 2),
                     "prob_below_zero": round(sum(1 for x in v if x < 0) / len(v), 3),
                     "prob_below_buffer": round(sum(1 for x in v if x < buffer) / len(v), 3)})

    lowest_p50 = min(days, key=lambda r: r["p50"])
    lowest_p10 = min(days, key=lambda r: r["p10"])
    first_neg = next((r["date"] for r in days if r["p50"] < 0), None)
    first_risk = next((r["date"] for r in days if r["prob_below_buffer"] >= config.WARNING_PROBABILITY), None)
    prob_any_neg = sum(1 for m in min_paths if m < 0) / len(min_paths)
    if first_neg or prob_any_neg >= 0.5:
        level = "critical"
    elif first_risk:
        level = "warning"
    else:
        level = "ok"
    projected_sales_28 = sum(model.point(asof, i) for i in range(1, 29))
    last_sales_28 = ledger.total(asof - timedelta(days=27), asof, "sales")
    summary = {
        "alert_level": level, "start_balance": round(start_balance, 2),
        "lowest_p50": {"date": lowest_p50["date"], "balance": lowest_p50["p50"]},
        "lowest_p10": {"date": lowest_p10["date"], "balance": lowest_p10["p10"]},
        "first_negative_date_p50": first_neg, "first_risk_date": first_risk,
        "probability_negative_any_day": round(prob_any_neg, 3),
        "days_until_risk": (date.fromisoformat(first_risk) - asof).days if first_risk else None,
        "gap_to_buffer_p10": round(max(0.0, buffer - lowest_p10["p10"]), 2),
        "gap_to_buffer_p50": round(max(0.0, buffer - lowest_p50["p50"]), 2),
        "shortfall_p50": round(max(0.0, -lowest_p50["p50"]), 2),
        "projected_sales_next_28d": round(projected_sales_28, 2),
        "actual_sales_last_28d": round(last_sales_28, 2),
        "level_uncertainty": round(level_sigma, 4),
        "calibration_past_forecasts": calibration["past_forecasts"],
    }
    assumptions = [
        "Sales follow the weekday pattern of the last 4 weeks with a damped trend. Closed days stay at zero.",
        "One-off large sales (for example event orders) do not raise the expected level. They enter the "
        "ranges at the rate they happened over the last 12 weeks.",
        f"Recurring payments found in the ledger: {len(recurring)}. Owner commitments used: {len(commitments)}.",
        f"{simulations} simulated futures. Ranges are p10 to p90, so 8 in 10 outcomes fall inside.",
        "A seasonal change the model has not seen yet is covered only by the extra uncertainty terms.",
        "Bills that change from month to month are drawn from their recent amounts in each simulated future.",
        f"Range width is set from this shop's own past forecast errors ({calibration['past_forecasts']} past "
        f"14-day forecasts), so a lumpier business gets wider ranges.",
        "Card settlement delays and customer refunds are not modelled.",
    ]
    return Forecast(asof, start_balance, buffer, days, summary, schedule, assumptions)


# ----------------------------------------------------------------------------- backtest
def wilson(hits: int, n: int, z: float = 1.96) -> list:
    """95% interval for a share, so a small number of origins is not over-read."""
    if n == 0:
        return [0.0, 0.0]
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)]


def backtest(ledger, horizon: int = 28, first_offset: int = 70, step: int = 1,
             simulations: int = 300, seed: int = config.SEED) -> dict:
    """Rolling-origin test. Weekly sales against two simple baselines, the balance against a naive
    balance forecast, and how often the real balance falls inside the p10 to p90 range."""
    start0 = ledger.txns[0].date
    last_origin = ledger.as_of - timedelta(days=horizon)
    origin = start0 + timedelta(days=first_offset)
    err = {"model": [], "flat_28d_mean": [], "repeat_last_week": []}
    bias = {"model": [], "flat_28d_mean": [], "repeat_last_week": []}
    bal_err = {14: {"model": [], "naive": []}, 28: {"model": [], "naive": []}}
    cover = {14: [], 28: []}
    origins = 0
    while origin <= last_origin:
        origins += 1
        model = fit_sales_model(ledger, origin)
        actual = ledger.daily(origin + timedelta(days=1), origin + timedelta(days=horizon), "sales")
        flat = statistics.fmean(ledger.daily(origin - timedelta(days=27), origin, "sales").values())
        last_week = sum(ledger.daily(origin - timedelta(days=6), origin, "sales").values())
        for w in range(horizon // 7):
            idxs = range(w * 7 + 1, w * 7 + 8)
            act = sum(actual[origin + timedelta(days=i)] for i in idxs)
            preds = {"model": sum(model.point(origin, i) for i in idxs),
                     "flat_28d_mean": flat * 7, "repeat_last_week": last_week}
            for k, p in preds.items():
                err[k].append(abs(p - act) / act)
                bias[k].append((p - act) / act)
        known = [{"date": t.date.isoformat(), "label": "known tax payment", "amount": -t.amount}
                 for t in ledger.txns if t.category == "tax"
                 and origin < t.date <= origin + timedelta(days=horizon)]
        f = forecast(ledger, asof=origin, horizon=horizon, simulations=simulations,
                     seed=seed, commitments=known)
        # Naive balance forecast: the average daily net cash of the last 28 days (tax left out),
        # carried forward, minus the same known tax payments the model receives.
        b0 = ledger.balance_on(origin)
        recent = [t for t in ledger.between(origin - timedelta(days=27), origin) if t.category != "tax"]
        daily_net = sum(t.amount for t in recent) / 28
        for offset in (14, 28):
            real = ledger.balance_on(origin + timedelta(days=offset))
            row = f.days[offset - 1]
            cover[offset].append(row["p10"] <= real <= row["p90"])
            tax = sum(c["amount"] for c in known
                      if date.fromisoformat(c["date"]) <= origin + timedelta(days=offset))
            bal_err[offset]["model"].append(abs(row["p50"] - real))
            bal_err[offset]["naive"].append(abs(b0 + daily_net * offset - tax - real))
        origin += timedelta(days=step)
    pct = lambda xs: round(100 * statistics.fmean(xs), 2)
    mae = lambda xs: round(statistics.fmean(xs), 0)
    return {
        "shop": ledger.profile.get("name", ""), "currency_symbol": ledger.profile.get("currency_symbol", "£"),
        "origins": origins, "origin_step_days": step,
        "horizon_days": horizon,
        "weekly_sales_mape_pct": {k: pct(v) for k, v in err.items()},
        "weekly_sales_bias_pct": {k: pct(v) for k, v in bias.items()},
        "balance_mae_gbp": {f"day_{o}": {k: mae(v) for k, v in bal_err[o].items()} for o in (14, 28)},
        "range_coverage_pct": {"balance_day_14": pct(cover[14]), "balance_day_28": pct(cover[28]),
                               "nominal": 80.0},
        "range_coverage_95ci": {"balance_day_14": wilson(sum(cover[14]), len(cover[14])),
                                "balance_day_28": wilson(sum(cover[28]), len(cover[28]))},
        "notes": ["Origins move one day at a time and overlap, so results are not independent. "
                  "The 95% intervals treat them as independent and are therefore too narrow.",
                  "Known tax payments are given to the forecast and to the naive baseline as owner "
                  "commitments, as a real owner would."],
    }
