"""Permission tiers and the rules that enforce them.

READ        read-only analysis. Always allowed.
PROPOSE     create a draft. Nothing leaves the sandbox. Always allowed.
EXECUTE     change something (simulated here). Needs a one-time confirmation code from the owner.
PROHIBITED  never allowed for the agent, for example moving money out.

The agent can never reach EXECUTE by itself. Code, not the model, enforces this.
"""
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum

from . import config


class Tier(IntEnum):
    READ = 0
    PROPOSE = 1
    EXECUTE = 2
    PROHIBITED = 3


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


def action_hash(tool: str, args: dict) -> str:
    body = json.dumps({"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class Policy:
    def __init__(self, clock=time.time, max_application=config.MAX_APPLICATION_GBP,
                 max_actions_per_day=config.MAX_EXECUTED_ACTIONS_PER_DAY,
                 ttl_seconds=config.CONFIRMATION_TTL_SECONDS, currency_symbol="£"):
        self.clock = clock
        self.symbol = currency_symbol
        self.max_application = max_application
        self.max_actions_per_day = max_actions_per_day
        self.ttl = ttl_seconds
        self._tokens = {}
        self._executed = {}

    def _day(self) -> str:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc).date().isoformat()

    def check_call(self, spec, origin: str, redeemed: bool = False) -> Decision:
        if spec.tier == Tier.PROHIBITED:
            return Decision(False, f"'{spec.name}' is never allowed for the agent.")
        if spec.tier == Tier.EXECUTE and not (origin == "owner" and redeemed):
            return Decision(False, f"'{spec.name}' needs the owner's confirmation code.")
        return Decision(True)

    def issue_token(self, tool: str, args: dict) -> str:
        token = secrets.token_hex(6)
        self._tokens[token] = {"hash": action_hash(tool, args), "expires": self.clock() + self.ttl, "used": False}
        return token

    def revoke(self, token: str) -> bool:
        return self._tokens.pop(token, None) is not None

    def check_limits(self, amount=None) -> Decision:
        if amount is not None and amount > self.max_application:
            return Decision(False, f"The amount {self.symbol}{amount:,.0f} is above the {self.symbol}{self.max_application:,.0f} limit "
                                   f"for actions the agent may submit.")
        if self._executed.get(self._day(), 0) >= self.max_actions_per_day:
            return Decision(False, f"The daily limit of {self.max_actions_per_day} executed actions is reached.")
        return Decision(True)

    def redeem(self, token: str, tool: str, args: dict) -> Decision:
        rec = self._tokens.get(token)
        if rec is None:
            return Decision(False, "Unknown confirmation code.")
        if rec["used"]:
            return Decision(False, "This confirmation code was already used.")
        if self.clock() > rec["expires"]:
            return Decision(False, "This confirmation code has expired.")
        if rec["hash"] != action_hash(tool, args):
            return Decision(False, "This code belongs to a different action.")
        rec["used"] = True
        self._executed[self._day()] = self._executed.get(self._day(), 0) + 1
        return Decision(True)
