"""Financing matching.

Step 1: hard filters. A product that fails any rule is rejected and the reason is kept.
Step 2: scoring. Eligible products get a 0 to 100 score from cost, speed, affordability,
        purpose fit and risk. Weights are in WEIGHTS and can be changed in one place.
Step 3: a recommendation is made only if the best product is affordable from the
        business's average monthly free cash flow. Otherwise the result says why not.
        With max_burden_share set (the live copilot uses config.MAX_REPAYMENT_SHARE), a product whose
        monthly repayment would take more than that share of free cash flow is first tried over a
        longer term, within its own limits. If no term brings it under the cap, it is not recommended.

A tax payment plan (HMRC Time to Pay) is not a loan. No money is paid to the business: part of a tax
bill is paid later, in instalments, with interest. Its reasons and the plan test say so.

Stock purchases are treated differently in the affordability check. Stock is sold, and the sale
repays most of the loan, so only the cost of credit plus a share of the principal that might not
sell in time (STOCK_UNSOLD_SHARE) has to come from free cash flow. This applies to terms of up to
STOCK_MAX_TERM_MONTHS. Longer stock loans are judged like any other loan.
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path

CREDIT_RANK = {"weak": 0, "fair": 1, "good": 2}
WEIGHTS = {"cost": 0.35, "speed": 0.20, "affordability": 0.25, "purpose": 0.10, "risk": 0.10}
LATE_PENALTY_PER_DAY = 15.0
AFFORDABLE_FULL = 0.30      # burden up to 30% of free cash flow scores 100
AFFORDABLE_ZERO = 1.00      # burden at 100% of free cash flow scores 0
STOCK_UNSOLD_SHARE = 0.25   # planning assumption: a quarter of the stock may not sell within the term
STOCK_MAX_TERM_MONTHS = 12  # self-repaying treatment only for short stock finance


@dataclass
class Business:
    months_trading: int
    annual_revenue: float
    credit_band: str
    monthly_free_cash_flow: float
    card_share: float = 0.0
    receivables: float = 0.0
    collateral: bool = False
    supplier_relationship: bool = False
    sector: str = ""
    legal_form: str = ""                 # ltd, llp, sole_trader or partnership
    existing_providers: tuple = ()       # banks and card processors the business already uses
    tax_due: float = 0.0                 # tax owed to the tax authority within the forecast window
    currency_symbol: str = "£"
    tax_authority: str = "HMRC"

    @property
    def monthly_card_sales(self) -> float:
        return self.annual_revenue * self.card_share / 12

    @classmethod
    def from_profile(cls, profile: dict, monthly_free_cash_flow: float, tax_due: float = 0.0,
                     region: dict = None):
        region = region or {}
        return cls(months_trading=int(profile["months_trading"]),
                   annual_revenue=float(profile["annual_revenue"]), credit_band=profile["credit_band"],
                   monthly_free_cash_flow=float(monthly_free_cash_flow),
                   card_share=float(profile.get("card_share", 0)),
                   receivables=float(profile.get("receivables", 0)),
                   collateral=bool(profile.get("collateral", False)),
                   supplier_relationship=bool(profile.get("supplier_relationship", False)),
                   sector=profile.get("sector", ""), legal_form=profile.get("legal_form", ""),
                   existing_providers=tuple(profile.get("existing_providers", ())),
                   tax_due=float(tax_due),
                   currency_symbol=profile.get("currency_symbol", region.get("symbol", "£")),
                   tax_authority=region.get("tax_authority", "HMRC"))


@dataclass
class Need:
    amount: float
    term_months: int
    urgency_days: int
    purpose: str            # cash_gap, stock, growth or equipment


def load_catalog(path=None) -> list:
    """The live catalogue of sourced UK products."""
    p = Path(path) if path else Path(__file__).with_name("catalog.json")
    return json.loads(p.read_text(encoding="utf-8"))["products"]


def load_eval_catalog() -> list:
    """The frozen illustrative catalogue that the 30 labelled cases were written against."""
    return load_catalog(Path(__file__).with_name("eval_catalog.json"))


def gbp(x: float, symbol: str = "£") -> str:
    """Format an amount. The name is historical: the symbol comes from the shop's region."""
    return f"-{symbol}{abs(x):,.0f}" if x < 0 else f"{symbol}{x:,.0f}"


def _apr(c: dict, credit_band: str):
    if "apr_by_credit" in c:
        return c["apr_by_credit"].get(credit_band)
    return c["apr"]


def _fees(c: dict, amount: float, term: int) -> float:
    for tier in c.get("fee_tiers", ()):
        if amount <= tier["max_amount"]:
            if "monthly_gbp" in tier:
                return tier["monthly_gbp"] * term
            yearly = max(tier.get("annual_min_gbp", 0.0), amount * tier["annual_pct"] / 100)
            return yearly * math.ceil(term / 12)
    return amount * c.get("fee_pct", 0) / 100


def cost_of_credit(product: dict, amount: float, term: int, credit_band: str = "fair",
                   purpose: str = None) -> dict:
    c = product["cost"]
    if c["model"] == "amortising":
        apr = _apr(c, credit_band)
        if apr is None:
            raise ValueError(f"{product['id']} has no rate for a {credit_band} credit profile")
        r = apr / 1200
        pay = amount / term if r == 0 else amount * r / (1 - (1 + r) ** -term)
        cost = pay * term - amount + _fees(c, amount, term)
    elif c["model"] == "period_fee":
        cost = amount * c["fee_pct_per_month"] / 100 * term
    elif c["model"] == "invoice":
        invoices = amount / c["advance_rate"]
        cost = amount * c["discount_apr"] / 1200 * term + invoices * c["service_pct_per_month"] / 100 * term
    elif c["model"] == "factor":
        cost = amount * (c["factor_rate"] - 1)
    elif c["model"] == "flat_fee":
        cost = amount * c["fee_pct"] / 100
    else:
        raise ValueError(f"unknown cost model {c['model']}")
    repay = product.get("repay_fraction", 1.0)
    if purpose == "stock" and term <= STOCK_MAX_TERM_MONTHS:
        repay = min(repay, STOCK_UNSOLD_SHARE)   # the stock's own sales repay the rest
    burden = (amount * repay + cost) / term
    return {"total_cost": round(cost, 2), "total_repayment": round(amount + cost, 2),
            "monthly_burden": round(burden, 2),
            "cost_pct_per_year": round(100 * cost / amount / (term / 12), 2)}


def check_eligibility(product: dict, biz: Business, need: Need) -> list:
    """Return the list of reasons the product is not eligible. Empty list means eligible."""
    why = []
    _m = lambda x: gbp(x, biz.currency_symbol)
    if need.amount < product["min_amount"]:
        why.append(f"The minimum amount is {_m(product['min_amount'])}, you need {_m(need.amount)}.")
    if need.amount > product["max_amount"]:
        why.append(f"The maximum amount is {_m(product['max_amount'])}, you need {_m(need.amount)}.")
    if not product["min_term"] <= need.term_months <= product["max_term"]:
        why.append(f"Terms run from {product['min_term']} to {product['max_term']} months, "
                   f"you need {need.term_months}.")
    if biz.months_trading < product["min_months_trading"]:
        why.append(f"It needs {product['min_months_trading']} months of trading, you have {biz.months_trading}.")
    if product.get("max_months_trading") is not None and biz.months_trading > product["max_months_trading"]:
        why.append(f"It is for businesses trading {product['max_months_trading']} months or less.")
    if biz.annual_revenue < product["min_annual_revenue"]:
        why.append(f"It needs yearly revenue of {_m(product['min_annual_revenue'])}.")
    if product.get("max_annual_revenue") and biz.annual_revenue > product["max_annual_revenue"]:
        why.append(f"It is for businesses with revenue up to {_m(product['max_annual_revenue'])}.")
    if product.get("legal_forms") and biz.legal_form not in product["legal_forms"]:
        why.append("It is only for " + " and ".join(f.upper() for f in product["legal_forms"]) + " businesses.")
    bank = product.get("requires_existing_customer")
    if bank and bank not in biz.existing_providers:
        why.append(f"It is only for existing {bank} customers.")
    if product.get("requires_tax_due"):
        if biz.tax_due <= 0:
            why.append(f"It only covers tax owed to {biz.tax_authority}, and no tax bill is due.")
        elif need.amount > biz.tax_due:
            why.append(f"It only covers the {_m(biz.tax_due)} tax bill, you need {_m(need.amount)}.")
    if product.get("min_monthly_card_sales") and biz.monthly_card_sales < product["min_monthly_card_sales"]:
        why.append(f"It needs card sales of at least {_m(product['min_monthly_card_sales'])} a month.")
    if "apr_by_credit" in product["cost"] and product["cost"]["apr_by_credit"].get(biz.credit_band) is None \
            and CREDIT_RANK[biz.credit_band] >= CREDIT_RANK[product["min_credit"]]:
        why.append(f"There is no published rate for a {biz.credit_band} credit profile.")
    if CREDIT_RANK[biz.credit_band] < CREDIT_RANK[product["min_credit"]]:
        why.append(f"It needs a {product['min_credit']} credit profile or better.")
    if product.get("requires_collateral") and not biz.collateral:
        why.append("It needs an asset as security.")
    cover = product.get("requires_receivables_cover")
    if cover and biz.receivables * cover < need.amount:
        why.append(f"It lends against unpaid invoices. {_m(biz.receivables)} of invoices supports "
                   f"{_m(biz.receivables * cover)}.")
    if product.get("requires_card_share") and biz.card_share < product["requires_card_share"]:
        why.append(f"It needs at least {product['requires_card_share']:.0%} of sales paid by card.")
    if product.get("requires_supplier_relationship") and not biz.supplier_relationship:
        why.append("It needs an existing supplier relationship.")
    if need.purpose not in product["purposes"]:
        why.append("It is not offered for this purpose.")
    return why


def _score(product, costs, c_min, biz, need, weights):
    cost_s = 100.0 * c_min / max(costs["cost_pct_per_year"], c_min)
    late = max(0, product["speed_days"] - need.urgency_days)
    speed_s = max(0.0, 100.0 - LATE_PENALTY_PER_DAY * late)
    fcf = biz.monthly_free_cash_flow
    ratio = math.inf if fcf <= 0 else costs["monthly_burden"] / fcf
    if ratio <= AFFORDABLE_FULL:
        aff_s = 100.0
    elif ratio >= AFFORDABLE_ZERO:
        aff_s = 0.0
    else:
        aff_s = 100.0 * (1 - (ratio - AFFORDABLE_FULL) / (AFFORDABLE_ZERO - AFFORDABLE_FULL))
    if need.purpose in product["primary_purposes"]:
        purpose_s = 100.0
    else:
        purpose_s = 60.0
    risk_s = 100.0 * product["risk"]
    parts = {"cost": cost_s, "speed": speed_s, "affordability": aff_s, "purpose": purpose_s, "risk": risk_s}
    total = sum(weights[k] * v for k, v in parts.items())
    return total, parts, ratio, late


def is_tax_deferral(product: dict) -> bool:
    return bool(product.get("requires_tax_due"))


def affordable_term(product: dict, biz: Business, need: Need, max_share: float):
    """The shortest term, from the requested one up to the product's maximum, whose monthly burden is
    within max_share of free cash flow. Returns (term, costs) or (None, costs at the requested term)."""
    first = cost_of_credit(product, need.amount, need.term_months, biz.credit_band, need.purpose)
    fcf = biz.monthly_free_cash_flow
    if fcf <= 0:
        return None, first
    for term in range(need.term_months, product["max_term"] + 1):
        trial = Need(need.amount, term, need.urgency_days, need.purpose)
        if term != need.term_months and check_eligibility(product, biz, trial):
            continue
        c = first if term == need.term_months else cost_of_credit(product, need.amount, term, biz.credit_band,
                                                                    need.purpose)
        if c["monthly_burden"] <= max_share * fcf + 0.005:
            return term, c
    return None, first


def match(biz: Business, need: Need, catalog=None, weights=None, gate: bool = True,
          max_burden_share: float = None) -> dict:
    """weights and gate exist for the ablation in evaluation.py. Normal use leaves them alone."""
    catalog = catalog or load_catalog()
    weights = weights or WEIGHTS
    eligible, rejected = [], []
    _m = lambda x: gbp(x, biz.currency_symbol)
    for p in catalog:
        reasons = check_eligibility(p, biz, need)
        if reasons:
            rejected.append({"id": p["id"], "name": p["name"], "reasons": reasons})
        else:
            eligible.append(p)
    terms, over_cap = {}, set()
    costs = {}
    for p in eligible:
        if max_burden_share is None:
            terms[p["id"]] = need.term_months
            costs[p["id"]] = cost_of_credit(p, need.amount, need.term_months, biz.credit_band, need.purpose)
            continue
        term, c = affordable_term(p, biz, need, max_burden_share)
        if term is None:
            over_cap.add(p["id"])
            term = need.term_months
        terms[p["id"]], costs[p["id"]] = term, c
    c_min = max(0.5, min((c["cost_pct_per_year"] for c in costs.values()), default=0.5))
    ranked = []
    for p in eligible:
        term = terms[p["id"]]
        total, parts, ratio, late = _score(p, costs[p["id"]], c_min, biz, need, weights)
        c = costs[p["id"]]
        apr = _apr(p["cost"], biz.credit_band) if p["cost"]["model"] == "amortising" else None
        rate = f" Interest rate {apr:.2f}% a year." if apr is not None else ""
        days = "1 day" if p["speed_days"] == 1 else f"{p['speed_days']} days"
        reasons = []
        if is_tax_deferral(p):
            reasons.append(f"This is not a loan and no money is paid to you. {biz.tax_authority} would let you pay "
                           f"{_m(need.amount)} of your {_m(biz.tax_due)} tax bill in instalments over {term} months "
                           f"instead of on the due date.")
            reasons.append(f"Interest of about {_m(c['total_cost'])} over {term} months.{rate}")
            reasons.append(f"Usually agreed in about {days}" +
                           (", before the bill is due." if late == 0 else
                            f", {late} days after your {need.urgency_days}-day deadline."))
        else:
            reasons.append(f"Costs {_m(c['total_cost'])} in total over {term} months.{rate}")
            reasons.append(f"Arrives in about {days}" +
                           (", in time for your deadline." if late == 0 else
                            f", {late} days after your {need.urgency_days}-day deadline."))
        if term != need.term_months:
            reasons.append(f"Spread over {term} months instead of {need.term_months}, so the monthly "
                           f"repayment stays within {max_burden_share:.0%} of your average free cash flow.")
        if math.isinf(ratio):
            reasons.append("Your average monthly free cash flow is not positive, so repayments are not covered.")
        else:
            reasons.append(f"The monthly burden is {_m(c['monthly_burden'])}, "
                           f"{ratio:.0%} of your average monthly free cash flow.")
        if need.purpose == "stock" and need.term_months <= STOCK_MAX_TERM_MONTHS:
            reasons.append(f"For stock, sales of the stock are expected to repay most of it. The burden counts "
                           f"the cost and {STOCK_UNSOLD_SHARE:.0%} of the amount in case some stock does not sell.")
        basis = p["cost"].get("rate_basis")
        if basis == "from":
            reasons.append("Costed at the lowest advertised rate. Your offer may be higher.")
        elif basis == "range":
            reasons.append("The provider publishes only a rate range. The rate used is an assumption, see the source.")
        elif basis == "index":
            reasons.append("Costed at a market average rate, not one lender's offer.")
        reasons.append(p["notes"])
        src = p.get("source") or {}
        ranked.append({"id": p["id"], "name": p["name"], "score": round(total, 1), "term_months": term,
                       "tax_deferral": is_tax_deferral(p), "within_cap": p["id"] not in over_cap,
                       "source_url": src.get("url"), "source_checked": src.get("checked"),
                       "breakdown": {k: round(v, 1) for k, v in parts.items()},
                       "cost": c, "affordability_ratio": None if math.isinf(ratio) else round(ratio, 3),
                       "speed_days": p["speed_days"], "reasons": reasons})
    ranked.sort(key=lambda r: (-r["score"], r["cost"]["total_cost"]))

    recommendation, note = None, None
    if not ranked:
        note = "No product in the catalogue fits this business and this need. See the reasons for each product."
    elif max_burden_share is not None:
        fits = [r for r in ranked if r["within_cap"] and r["affordability_ratio"] is not None]
        if fits:
            recommendation = fits[0]
            if fits[0] is not ranked[0]:
                note = (f"{ranked[0]['name']} scored higher, but its repayment would take more than "
                        f"{max_burden_share:.0%} of your average monthly free cash flow, so it is not recommended.")
        elif biz.monthly_free_cash_flow <= 0:
            note = (f"Your average monthly free cash flow is {_m(biz.monthly_free_cash_flow)}, so the business "
                    f"has nothing spare to repay from. I am not recommending any borrowing. Cutting costs or "
                    f"raising sales comes first.")
        else:
            top = ranked[0]
            note = (f"No option keeps the monthly repayment within {max_burden_share:.0%} of your average monthly "
                    f"free cash flow ({_m(biz.monthly_free_cash_flow)}). The closest is {top['name']}. "
                    f"Borrowing less, or cutting costs first, is safer than taking it.")
    else:
        top = ranked[0]
        if gate and (top["affordability_ratio"] is None or top["affordability_ratio"] > 1.0):
            note = (f"The best match, {top['name']}, would cost more each month than your average free cash "
                    f"flow. Borrowing a smaller amount or for longer is safer than taking it.")
        else:
            recommendation = top
    return {"need": {"amount": need.amount, "term_months": need.term_months,
                     "urgency_days": need.urgency_days, "purpose": need.purpose},
            "ranked": ranked, "rejected": rejected, "recommendation": recommendation, "note": note}


def need_from_forecast(fc, purpose: str = "cash_gap", term_months: int = 6) -> Need:
    """Size a cash-gap need from the forecast: the expected gap to the buffer plus 25 percent headroom."""
    s = fc.summary
    gap = s["gap_to_buffer_p50"]
    amount = math.ceil(gap * 1.25 / 500) * 500 if gap > 0 else 0
    horizon = s["days_until_risk"]
    if horizon is None:
        horizon = 30
    return Need(amount=amount, term_months=term_months, urgency_days=max(1, horizon - 3), purpose=purpose)
