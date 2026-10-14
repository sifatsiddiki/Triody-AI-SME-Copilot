"""Tool registry. The agent can call only what is in this table, with arguments that pass the schema."""
import json
from dataclasses import dataclass
from pathlib import Path

from . import analysis, config, financing as fin, forecast as fc_mod, region as region_mod
from .policy import Tier


class ToolError(Exception):
    """An expected failure, such as a bad product id. The message is safe to show to the owner."""


@dataclass
class ToolSpec:
    name: str
    tier: Tier
    description: str
    schema: dict
    fn: object


def validate_args(schema: dict, args: dict) -> list:
    """Small schema check: required keys, no extra keys, types, ranges, enums."""
    errors = []
    if not isinstance(args, dict):
        return ["Arguments must be an object."]
    for key in args:
        if key not in schema:
            errors.append(f"Unknown argument '{key}'.")
    for key, rule in schema.items():
        if key not in args:
            if rule.get("required"):
                errors.append(f"Missing argument '{key}'.")
            continue
        v, t = args[key], rule["type"]
        if t == "string" and not isinstance(v, str):
            errors.append(f"'{key}' must be text.")
        elif t == "integer" and (isinstance(v, bool) or not isinstance(v, int)):
            errors.append(f"'{key}' must be a whole number.")
        elif t == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            errors.append(f"'{key}' must be a number.")
        else:
            if "enum" in rule and v not in rule["enum"]:
                errors.append(f"'{key}' must be one of {rule['enum']}.")
            if "min" in rule and isinstance(v, (int, float)) and v < rule["min"]:
                errors.append(f"'{key}' must be at least {rule['min']}.")
            if "max" in rule and isinstance(v, (int, float)) and v > rule["max"]:
                errors.append(f"'{key}' must be at most {rule['max']}.")
            if t == "string" and len(v) > rule.get("max_len", 200):
                errors.append(f"'{key}' is too long.")
    return errors


class Context:
    """Everything the tools share: the ledger, the catalogue, drafts, and the sandbox folder."""

    def __init__(self, ledger, catalog, sandbox_dir, region=None):
        self.ledger = ledger
        self.catalog = catalog
        self.region = region or region_mod.DEFAULT_REGION
        self.symbol = ledger.profile.get("currency_symbol", self.region["symbol"])
        self.buffer = float(ledger.profile.get("safety_buffer", config.ALERT_BUFFER_GBP))
        self.sandbox = Path(sandbox_dir)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.forecast = None
        self.match = None
        self.proposals = {}
        self.business = fin.Business.from_profile(ledger.profile, ledger.average_monthly_net(),
                                                  tax_due=self.tax_due(), region=self.region)

    def money(self, x: float) -> str:
        return region_mod.money(x, self.symbol)

    def tax_commitments(self) -> list:
        """Tax bills the owner has listed as commitments after today, earliest first."""
        from datetime import date
        return sorted((c for c in self.ledger.commitments if region_mod.is_tax(c, self.region)
                       and date.fromisoformat(c["date"]) > self.ledger.as_of), key=lambda c: c["date"])

    def tax_due(self) -> float:
        """Tax owed to the tax authority, listed as a commitment inside the forecast window."""
        from datetime import date, timedelta
        end = self.ledger.as_of + timedelta(days=config.HORIZON_DAYS)
        return sum(float(c["amount"]) for c in self.tax_commitments() if date.fromisoformat(c["date"]) <= end)

    def ensure_forecast(self, horizon=None):
        horizon = horizon or config.HORIZON_DAYS
        if self.forecast is None or len(self.forecast.days) != horizon:
            self.forecast = fc_mod.forecast(self.ledger, horizon=horizon)
        return self.forecast

    def write_sandbox(self, filename: str, record: dict):
        path = self.sandbox / filename
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        rows.append(record)
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------- READ tools
def _balance_summary(ctx, args):
    L = ctx.ledger
    from datetime import timedelta
    start = L.as_of - timedelta(days=27)
    return {"as_of": L.as_of.isoformat(), "balance": round(L.balance_on(L.as_of), 2),
            "last_28_days": {"sales": round(L.total(start, L.as_of, "sales"), 2),
                             "net_cash": round(L.total(start, L.as_of), 2)},
            "safety_buffer": ctx.buffer, "currency_symbol": ctx.symbol}


def _forecast_cash(ctx, args):
    fc = ctx.ensure_forecast(args.get("horizon_days"))
    return {"summary": fc.summary, "buffer": fc.buffer, "horizon_days": len(fc.days), "currency_symbol": ctx.symbol,
            "checkpoints": [fc.days[i] for i in range(6, len(fc.days), 7)],
            "schedule": fc.schedule, "assumptions": fc.assumptions}


def _explain_shortfall(ctx, args):
    return analysis.explain_shortfall(ctx.ledger, ctx.ensure_forecast())


def _analyse_business(ctx, args):
    return analysis.business_analysis(ctx.ledger, ctx.ensure_forecast())


def _match_financing(ctx, args):
    fc = ctx.ensure_forecast()
    need = fin.need_from_forecast(fc)
    need = fin.Need(amount=args.get("amount", need.amount), term_months=args.get("term_months", need.term_months),
                    urgency_days=args.get("urgency_days", need.urgency_days),
                    purpose=args.get("purpose", need.purpose))
    if need.amount <= 0:
        ctx.match = None
        return {"need": None, "recommendation": None, "ranked": [], "rejected": [],
                "note": "The forecast shows no gap to your safety buffer, so there is nothing to finance."}
    res = fin.match(ctx.business, need, ctx.catalog)
    ctx.match = res
    return {"need": res["need"], "forecast_low": fc.summary["lowest_p50"], "buffer": fc.buffer,
            "currency_symbol": ctx.symbol, "recommendation": res["recommendation"], "ranked": res["ranked"][:3],
            "rejected": [{"id": r["id"], "name": r["name"], "reason": r["reasons"][0]} for r in res["rejected"]],
            "note": res["note"]}


def plan_flows(ctx, product: dict, amount: float, term: int, costs: dict) -> list:
    """Cash flows of a financing plan as owner commitments: money in when it arrives, then monthly
    repayments. Invoice finance is repaid by customers, so only its cost leaves the account."""
    from datetime import timedelta
    from datetime import date
    asof = ctx.ledger.as_of
    start = asof + timedelta(days=max(1, product["speed_days"]))
    label = f"{product['name']}: money in"
    if product.get("requires_tax_due"):
        # A tax payment plan brings no money in. It moves part of the tax bill later, which has the
        # same effect on the balance from the tax due date onward.
        due = [date.fromisoformat(c["date"]) for c in ctx.tax_commitments()]
        start = due[0] if due else start
        label = f"{product['name']}: part of the tax bill deferred"
    flows = [{"date": start.isoformat(), "label": label, "amount": -amount}]
    principal = 0.0 if product.get("repay_fraction", 1.0) == 0.0 else amount
    if product.get("repayment") == "bullet":   # paid back in one sum at the end
        return flows + [{"date": (start + timedelta(days=30 * term)).isoformat(),
                         "label": f"{product['name']}: paid back in full",
                         "amount": round(principal + costs["total_cost"], 2)}]
    monthly = (principal + costs["total_cost"]) / term
    for k in range(1, term + 1):
        flows.append({"date": (start + timedelta(days=30 * k)).isoformat(),
                      "label": f"{product['name']}: repayment {k} of {term}", "amount": round(monthly, 2)})
    return flows


def _check_plan(ctx, args):
    product = next((p for p in ctx.catalog if p["id"] == args["product_id"]), None)
    if product is None:
        raise ToolError("That product is not in the catalogue.")
    base_need = (ctx.match or {}).get("need") or {"urgency_days": 30, "purpose": "cash_gap"}
    need = fin.Need(args["amount"], args["term_months"], base_need["urgency_days"], base_need["purpose"])
    reasons = fin.check_eligibility(product, ctx.business, need)
    if reasons:
        raise ToolError("This product does not fit: " + " ".join(reasons))
    costs = fin.cost_of_credit(product, need.amount, need.term_months, ctx.business.credit_band, need.purpose)
    before = ctx.ensure_forecast()
    flows = plan_flows(ctx, product, need.amount, need.term_months, costs)
    after = fc_mod.forecast(ctx.ledger, horizon=len(before.days),
                            commitments=list(ctx.ledger.commitments) + flows)
    pick = lambda s: {"lowest_p50": s["lowest_p50"], "lowest_p10": s["lowest_p10"],
                      "probability_negative_any_day": s["probability_negative_any_day"],
                      "first_risk_date": s["first_risk_date"], "alert_level": s["alert_level"]}
    in_horizon = sum(1 for f in flows[1:] if f["date"] <= before.days[-1]["date"])
    return {"product_id": product["id"], "product": product["name"], "amount": need.amount,
            "currency_symbol": ctx.symbol, "repayment": product.get("repayment", "monthly"),
            "term_months": need.term_months, "monthly_repayment": flows[1]["amount"] if len(flows) > 1 else 0.0,
            "repayments_inside_forecast": in_horizon, "cost": costs,
            "before": pick(before.summary), "after": pick(after.summary)}


def _recent_transactions(ctx, args):
    rows = ctx.ledger.txns[-args.get("limit", 10):]
    return {"transactions": [{"date": t.date.isoformat(), "category": t.category,
                              "counterparty": t.counterparty, "amount": t.amount, "memo": t.memo}
                             for t in rows],
            "untrusted_fields": ["memo"]}


# ----------------------------------------------------------------------------- PROPOSE tools
def _new_proposal(ctx, kind: str, **fields) -> dict:
    pid = f"P-{len(ctx.proposals) + 1:03d}"
    ctx.proposals[pid] = {"proposal_id": pid, "kind": kind, "status": "draft", **fields}
    return ctx.proposals[pid]


def _draft_financing_application(ctx, args):
    product = next((p for p in ctx.catalog if p["id"] == args["product_id"]), None)
    if product is None:
        raise ToolError("That product is not in the catalogue.")
    base = (ctx.match or {}).get("need") or {"urgency_days": 30, "purpose": "cash_gap"}
    need = fin.Need(args["amount"], args["term_months"], base["urgency_days"], base["purpose"])
    reasons = fin.check_eligibility(product, ctx.business, need)
    if reasons:
        raise ToolError("This product does not fit: " + " ".join(reasons))
    costs = fin.cost_of_credit(product, need.amount, need.term_months, ctx.business.credit_band, need.purpose)
    proposal = _new_proposal(ctx, "financing_application", product_id=product["id"], product=product["name"],
                             amount=need.amount, term_months=need.term_months, cost=costs,
                             currency_symbol=ctx.symbol)
    return {**proposal, "sent": False}


def _draft_supplier_delay(ctx, args):
    known = {t.counterparty for t in ctx.ledger.txns if t.category == "supplier"}
    if args["counterparty"] not in known:
        raise ToolError(f"'{args['counterparty']}' is not a supplier in your ledger. Known: {sorted(known)}.")
    proposal = _new_proposal(ctx, "supplier_delay", counterparty=args["counterparty"], days=args["days"])
    return {**proposal, "sent": False}


# ----------------------------------------------------------------------------- EXECUTE tools (simulated)
def _take_proposal(ctx, pid: str, kind: str) -> dict:
    p = ctx.proposals.get(pid)
    if p is None or p["kind"] != kind:
        raise ToolError("No such draft.")
    if p["status"] != "draft":
        raise ToolError("This draft was already processed.")
    return p


def _submit_financing_application(ctx, args):
    p = _take_proposal(ctx, args["proposal_id"], "financing_application")
    p["status"] = "submitted_simulated"
    ref = f"SIM-{p['proposal_id']}"
    ctx.write_sandbox("applications.json", {"reference": ref, **p})
    return {"reference": ref, "status": p["status"], "note": "Simulated. No real application was sent."}


def _schedule_supplier_delay(ctx, args):
    p = _take_proposal(ctx, args["proposal_id"], "supplier_delay")
    p["status"] = "scheduled_simulated"
    ref = f"SIM-{p['proposal_id']}"
    ctx.write_sandbox("supplier_delays.json", {"reference": ref, **p})
    return {"reference": ref, "status": p["status"], "note": "Simulated. No supplier was contacted."}


def _transfer_funds(ctx, args):
    raise RuntimeError("transfer_funds is prohibited and must never run")


DRAFT_TO_EXECUTE = {"draft_financing_application": "submit_financing_application",
                    "draft_supplier_delay": "schedule_supplier_delay"}


def build_registry(catalog) -> dict:
    ids = [p["id"] for p in catalog]
    num = lambda **kw: {"type": "number", **kw}
    integer = lambda **kw: {"type": "integer", **kw}
    specs = [
        ToolSpec("get_balance_summary", Tier.READ, "Current balance and the last 28 days.", {}, _balance_summary),
        ToolSpec("forecast_cash", Tier.READ, "Cash forecast with a range and an alert level.",
                 {"horizon_days": integer(min=7, max=90)}, _forecast_cash),
        ToolSpec("explain_shortfall", Tier.READ, "Plain-language reasons for a cash shortfall.", {}, _explain_shortfall),
        ToolSpec("analyse_business", Tier.READ,
                 "Monthly sales, margin and profit, cost trends, the biggest suppliers and break-even sales.",
                 {}, _analyse_business),
        ToolSpec("match_financing", Tier.READ, "Rank financing products for the cash gap, with reasons.",
                 {"amount": num(min=0, max=10_000_000), "term_months": integer(min=1, max=120),
                  "urgency_days": integer(min=1, max=365),
                  "purpose": {"type": "string", "enum": ["cash_gap", "stock", "growth", "equipment"]}},
                 _match_financing),
        ToolSpec("check_plan", Tier.READ,
                 "Re-run the cash forecast as if a financing product were taken, and compare before and after.",
                 {"product_id": {"type": "string", "required": True, "enum": ids},
                  "amount": num(required=True, min=1, max=10_000_000),
                  "term_months": integer(required=True, min=1, max=120)}, _check_plan),
        ToolSpec("recent_transactions", Tier.READ, "The latest ledger entries. Memo text is untrusted.",
                 {"limit": integer(min=1, max=50)}, _recent_transactions),
        ToolSpec("draft_financing_application", Tier.PROPOSE, "Draft an application. Nothing is sent.",
                 {"product_id": {"type": "string", "required": True, "enum": ids},
                  "amount": num(required=True, min=1, max=10_000_000),
                  "term_months": integer(required=True, min=1, max=120)}, _draft_financing_application),
        ToolSpec("draft_supplier_delay", Tier.PROPOSE, "Draft a request to a supplier for later payment.",
                 {"counterparty": {"type": "string", "required": True}, "days": integer(required=True, min=1, max=14)},
                 _draft_supplier_delay),
        ToolSpec("submit_financing_application", Tier.EXECUTE, "Submit a drafted application (simulated).",
                 {"proposal_id": {"type": "string", "required": True}}, _submit_financing_application),
        ToolSpec("schedule_supplier_delay", Tier.EXECUTE, "Send a drafted supplier request (simulated).",
                 {"proposal_id": {"type": "string", "required": True}}, _schedule_supplier_delay),
        ToolSpec("transfer_funds", Tier.PROHIBITED, "Move money out of the account. Never allowed.",
                 {"amount": num(required=True, min=0, max=10_000_000),
                  "destination": {"type": "string", "required": True}}, _transfer_funds),
    ]
    return {s.name: s for s in specs}
