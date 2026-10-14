import unittest
from datetime import date

from tests.helpers import ROOT, make_copilot  # noqa: F401
from triody import forecast as F, synth
from triody.ledger import Ledger


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = Ledger.from_dir(ROOT / "data")
        cls.fc = F.forecast(cls.ledger)

    def test_same_seed_same_result(self):
        again = F.forecast(self.ledger)
        self.assertEqual(self.fc.summary, again.summary)

    def test_synthetic_data_is_reproducible(self):
        a, b = synth.generate(), synth.generate()
        self.assertEqual(a["transactions"], b["transactions"])

    def test_ledger_balance_matches_target(self):
        self.assertAlmostEqual(self.ledger.balance_on(date(2026, 10, 14)), 9200.0, places=1)

    def test_alert_found_before_vat_is_due(self):
        s = self.fc.summary
        self.assertEqual(s["alert_level"], "critical")
        self.assertEqual(s["first_risk_date"], "2026-11-07")

    def test_ranges_are_ordered(self):
        for d in self.fc.days:
            self.assertLessEqual(d["p10"], d["p50"])
            self.assertLessEqual(d["p50"], d["p90"])

    def test_recurring_payments_are_found(self):
        names = {s["counterparty"] for s in self.fc.schedule}
        for expected in ("Payroll", "Hanover Estates", "Lothian Mill and Dairy", "Leith Coffee Roasters"):
            self.assertIn(expected, names)

    def test_forecast_ignores_future_ledger_rows(self):
        """A forecast made at an earlier date must not change if later rows are removed."""
        origin = date(2026, 9, 1)
        full = F.forecast(self.ledger, asof=origin, horizon=21, simulations=200, commitments=[])
        trimmed = Ledger([t for t in self.ledger.txns if t.date <= origin],
                         self.ledger.opening_balance, origin, [], self.ledger.profile)
        cut = F.forecast(trimmed, asof=origin, horizon=21, simulations=200, commitments=[])
        self.assertEqual(full.summary, cut.summary)

    def test_healthy_shop_has_no_alert(self):
        rich = synth.generate(target_balance=60000.0)
        led = Ledger(
            [__import__("triody.ledger", fromlist=["Txn"]).Txn(date.fromisoformat(t["date"]), t["category"],
                                                               t["counterparty"], t["amount"], t["memo"])
             for t in rich["transactions"]],
            rich["opening_balance"], date.fromisoformat(rich["as_of"]), [], rich["profile"])
        self.assertEqual(F.forecast(led, simulations=300).summary["alert_level"], "ok")


if __name__ == "__main__":
    unittest.main()
