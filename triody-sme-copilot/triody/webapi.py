"""Request handling shared by the local server and the browser build.

The local server (server.py) and the in-browser version (webapp/) both call `dispatch`, so the
same checks and the same responses apply on a laptop, a phone, or any other device.
"""
import json

from . import analysis
from .audit import AuditLog

MAX_BODY = 4000


def state(copilot) -> dict:
    """Everything the dashboard shows. Approval codes appear only under owner_device, which the page
    draws in its own panel, standing in for the owner's phone. They are never part of a chat reply."""
    fc = copilot.ctx.ensure_forecast()
    bal = copilot.run_tool("get_balance_summary", {}).data
    return {"balance": bal, "forecast": {"summary": fc.summary, "days": fc.days, "buffer": fc.buffer,
                                          "schedule": fc.schedule},
            "pending": copilot.pending_view(), "owner_device": list(copilot.owner_device),
            "analysis": analysis.business_analysis(copilot.ctx.ledger, fc),
            "shop": copilot.ctx.ledger.profile.get("name", ""), "region": copilot.region["code"],
            "currency_symbol": copilot.ctx.symbol, "brain": copilot.brain_name}


def audit_view(copilot) -> dict:
    return {"entries": copilot.audit.entries()[-40:], "chain": AuditLog.verify(copilot.audit.path)}


def dispatch(copilot, method: str, path: str, raw_body: str = "") -> tuple:
    """Return (status, payload). `raw_body` is the JSON text of a POST body."""
    path = path.split("?")[0]
    if method == "GET":
        if path == "/api/state":
            return 200, state(copilot)
        if path == "/api/audit":
            return 200, audit_view(copilot)
        return 404, {"error": "not found"}
    if method != "POST":
        return 405, {"error": "method not allowed"}
    if not raw_body or len(raw_body.encode("utf-8")) > MAX_BODY:
        return 413, {"error": "body size not allowed"}
    try:
        body = json.loads(raw_body)
    except ValueError:
        return 400, {"error": "invalid json"}
    if not isinstance(body, dict):
        return 400, {"error": "invalid json"}
    if path == "/api/briefing":
        r = copilot.briefing()
        return 200, {"text": r.text, "state": state(copilot)}
    if path == "/api/chat" and isinstance(body.get("message"), str):
        r = copilot.handle(body["message"])
        return 200, {"text": r.text, "state": state(copilot)}
    if path == "/api/confirm" and isinstance(body.get("code"), str):
        r = copilot.confirm(body["code"].strip().lower()[:32])
        return 200, {"text": r.text, "state": state(copilot)}
    if path == "/api/cancel" and isinstance(body.get("proposal_id"), str):
        r = copilot.cancel(body["proposal_id"][:32])
        return 200, {"text": r.text, "state": state(copilot)}
    return 400, {"error": "unknown request"}


def dispatch_json(copilot, method: str, path: str, raw_body: str = "") -> str:
    """Browser entry point: returns one JSON string holding status and payload."""
    status, payload = dispatch(copilot, method, path, raw_body or "")
    return json.dumps({"status": status, "body": payload})
