"""Read-only view of a shop's transactions."""
import csv
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Txn:
    date: date
    category: str
    counterparty: str
    amount: float          # positive = money in, negative = money out
    memo: str = ""


class Ledger:
    def __init__(self, txns, opening_balance, as_of, commitments=(), profile=None):
        self.txns = sorted(txns, key=lambda t: t.date)
        self.opening_balance = float(opening_balance)
        self.as_of = as_of
        self.commitments = list(commitments)
        self.profile = dict(profile or {})
        self._balance = {}
        running = self.opening_balance
        if self.txns:
            d, last = self.txns[0].date, self.txns[-1].date
            by_day = {}
            for t in self.txns:
                by_day[t.date] = by_day.get(t.date, 0.0) + t.amount
            while d <= max(last, as_of):
                running += by_day.get(d, 0.0)
                self._balance[d] = running
                d += timedelta(days=1)

    @classmethod
    def from_dir(cls, path):
        p = Path(path)
        txns = []
        with open(p / "transactions.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                txns.append(Txn(date.fromisoformat(r["date"]), r["category"], r["counterparty"],
                                float(r["amount"]), r.get("memo", "")))
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        commitments = json.loads((p / "commitments.json").read_text(encoding="utf-8"))
        profile = json.loads((p / "business_profile.json").read_text(encoding="utf-8"))
        return cls(txns, meta["opening_balance"], date.fromisoformat(meta["as_of"]),
                   commitments, profile)

    def balance_on(self, d: date) -> float:
        if d < min(self._balance):
            return self.opening_balance
        return self._balance[min(d, max(self._balance))]

    def between(self, start: date, end: date):
        return [t for t in self.txns if start <= t.date <= end]

    def total(self, start: date, end: date, category=None, direction=None) -> float:
        """Sum of amounts in a window. direction: 'in', 'out' or None for net."""
        total = 0.0
        for t in self.between(start, end):
            if category is not None and t.category != category:
                continue
            if direction == "in" and t.amount <= 0:
                continue
            if direction == "out" and t.amount >= 0:
                continue
            total += t.amount
        return total

    def daily(self, start: date, end: date, category: str) -> dict:
        out = {}
        d = start
        while d <= end:
            out[d] = 0.0
            d += timedelta(days=1)
        for t in self.between(start, end):
            if t.category == category:
                out[t.date] += t.amount
        return out

    def average_monthly_net(self, days: int = 180) -> float:
        """Average monthly net cash over a long window. Used as through-the-cycle free cash flow."""
        end = self.as_of
        start = end - timedelta(days=days - 1)
        return self.total(start, end) * 30.4 / days
