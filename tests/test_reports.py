"""Period figures, replies through the whole copilot, and the financing fixes.

The figures are checked against sums taken straight from transactions.csv, so a change in the
report code that drifts from the ledger fails here.
"""
import csv
import unittest
from datetime import date

from tests.helpers import ROOT, make_copilot
from triody import config, reports
from triody.ledger import Ledger

GENERIC_HELP_START = "I can answer questions about your own books"


def csv_total(start, end, category=None):
    total = 0.0
    with open(ROOT / "data" / "transactions.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r["date"])
            if start <= d <= end and (category is None or r["category"] == category):
                total += float(r["amount"])
    return total


class PeriodFigureTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger.from_dir(ROOT / "data")

    def test_today_sales_match_the_ledger(self):
        r = reports.period_report(self.ledger, date(2026, 10, 14), date(2026, 10, 14))
        self.assertAlmostEqual(r["sales"], csv_total(date(2026, 10, 14), date(2026, 10, 14), "sales"), places=2)

    def test_october_is_not_september(self):
        oct_ = reports.period_report(self.ledger, date(2026, 10, 1), date(2026, 10, 31))
        sep = reports.period_report(self.ledger, date(2026, 9, 1), date(2026, 9, 30))
        self.assertAlmostEqual(oct_["sales"], csv_total(date(2026, 10, 1), date(2026, 10, 14), "sales"), places=2)
        self.assertAlmostEqual(sep["sales"], csv_total(date(2026, 9, 1), date(2026, 9, 30), "sales"), places=2)
        self.assertNotAlmostEqual(oct_["sales"], sep["sales"])
        self.assertEqual(oct_["end"], "2026-10-14")
        self.assertTrue(oct_["partial"])

    def test_this_year_starts_at_first_day_in_books(self):
        r = reports.period_report(self.ledger, date(2026, 1, 1), date(2026, 10, 14))
        self.assertEqual(r["start"], "2026-04-18")
        self.assertTrue(any("start on 18 Apr 2026" in n for n in r["notes"]))

    def test_costs_add_up(self):
        r = reports.period_report(self.ledger, date(2026, 9, 1), date(2026, 9, 30))
        self.assertAlmostEqual(r["total_costs"], sum(r["costs"].values()), places=1)
        self.assertAlmostEqual(r["net_cash"], csv_total(date(2026, 9, 1), date(2026, 9, 30)), places=2)
        self.assertAlmostEqual(r["costs"]["payroll"], -csv_total(date(2026, 9, 1), date(2026, 9, 30), "payroll"),
                               places=2)

    def test_future_and_old_periods_are_unavailable_not_zero(self):
        future = reports.period_report(self.ledger, date(2026, 11, 1), date(2026, 11, 30))
        old = reports.period_report(self.ledger, date(2025, 1, 1), date(2025, 12, 31))
        for r in (future, old):
            self.assertFalse(r["available"])
            self.assertNotIn("sales", r)
        self.assertIn("not happened yet", future["reason"])
        self.assertIn("start on", old["reason"])

    def test_reversed_range_is_refused(self):
        r = reports.period_report(self.ledger, date(2026, 10, 10), date(2026, 10, 1))
        self.assertFalse(r["available"])

    def test_overview_has_five_periods(self):
        o = reports.overview(self.ledger)
        self.assertEqual([r["label"].split(" (")[0] for r in o["overview"]][:3],
                         ["Today", "This week so far", "October so far"])


class ReplyTests(unittest.TestCase):
    """Whole-copilot replies for the questions that used to get the generic help text."""

    def setUp(self):
        self.c, _ = make_copilot()

    def reply(self, text):
        return self.c.handle(text).text

    def test_sales_questions_get_sales_figures(self):
        for q in ("Today's sales", "sales", "this year sale", "slaes today", "October sales"):
            with self.subTest(q=q):
                text = self.reply(q)
                self.assertFalse(text.startswith(GENERIC_HELP_START), q)
                self.assertIn("Sales", text)

    def test_today_and_month_and_year_differ(self):
        today, month, year = (self.reply(q) for q in ("sales today", "sales this month", "sales this year"))
        self.assertIn("today (Wednesday 14 Oct 2026)", today)
        self.assertIn("October 2026 so far", month)
        self.assertIn("this year so far", year)
        self.assertEqual(len({today, month, year}), 3)

    def test_october_reply_names_october(self):
        text = self.reply("how much did I sell in October?")
        self.assertIn("October 2026", text)
        self.assertNotIn("September 2026:", text.split("Compared")[0])

    def test_unclear_message_gets_a_question_back(self):
        text = self.reply("asdfgh")
        self.assertIn('"asdfgh"', text)
        self.assertFalse(text.startswith(GENERIC_HELP_START))

    def test_unsupported_data_says_what_is_missing(self):
        text = self.reply("how many customers came in last week?")
        self.assertIn("don't record customer numbers", text)

    def test_future_period_is_explained_and_forecast_offered(self):
        text = self.reply("sales in November")
        self.assertIn("not happened yet", text)
        self.assertIn("forecast", text.lower())

    def test_history_gap_is_explained(self):
        self.assertIn("Your books start on 18 Apr 2026", self.reply("sales last year"))

    def test_sme_and_data_questions(self):
        self.assertIn("fewer than 250 employees", self.reply("what is an SME?"))
        self.assertIn("Your books cover 18 Apr 2026 to 14 Oct 2026", self.reply("can I add more data?"))

    def test_follow_up_keeps_the_metric(self):
        self.reply("wages this month")
        self.assertIn("Wages in September 2026", self.reply("and last month?"))

    def test_typo_is_mentioned(self):
        self.assertIn('read "slaes" as "sales"', self.reply("slaes today"))

    def test_report_numbers_are_grounded(self):
        from triody.agent import numbers_grounded
        r = self.c.handle("costs last month")
        self.assertEqual(numbers_grounded(r.text, r.results), [])


class FinancingFixTests(unittest.TestCase):
    def setUp(self):
        self.c, _ = make_copilot()
        self.text = self.c.handle("What financing fits?").text
        self.match = self.c.ctx.match

    def test_time_to_pay_is_not_presented_as_money_received(self):
        rec = self.match["recommendation"]
        self.assertEqual(rec["id"], "hmrc_ttp")
        self.assertIn("not a loan and no money is paid to you", self.text)
        self.assertNotIn("Arrives in about", rec["reasons"][2])

    def test_time_to_pay_plan_moves_the_tax_bill_and_adds_no_cash(self):
        from triody.tools import plan_commitments
        from triody import financing as fin
        p = next(x for x in self.c.ctx.catalog if x["id"] == "hmrc_ttp")
        costs = fin.cost_of_credit(p, 5000, 10, "fair")
        commitments, repayments = plan_commitments(self.c.ctx, p, 5000, 10, costs)
        self.assertFalse(any(c["amount"] < 0 for c in commitments))           # nothing paid in
        vat = next(c for c in commitments if "VAT" in c["label"])
        self.assertAlmostEqual(vat["amount"], 1800.0)                        # 6,800 less 5,000 deferred
        self.assertEqual(len(repayments), 10)
        self.assertAlmostEqual(sum(r["amount"] for r in repayments), 5000 + costs["total_cost"], places=0)

    def test_repayment_stays_within_the_cap(self):
        rec = self.match["recommendation"]
        self.assertLessEqual(rec["affordability_ratio"], config.MAX_REPAYMENT_SHARE)
        self.assertNotIn("77%", self.text)

    def test_plan_test_counts_every_repayment(self):
        plan = next(r for r in self.c._run_rules("what financing fits") if r.tool == "check_plan")
        d = plan.data
        self.assertEqual(d["repayments_inside_full_test"], d["repayments"])
        self.assertGreaterEqual(d["full_term"]["end"], d["last_repayment"])
        self.assertIn(f"all {d['repayments']} payments counted", self.text)

    def test_draft_and_confirmation_use_the_adjusted_term(self):
        self.c.handle("apply")
        proposal = next(iter(self.c.pending.values()))["proposal"]
        self.assertEqual(proposal["term_months"], self.match["recommendation"]["term_months"])
        self.assertTrue(proposal["tax_deferral"])
        token = next(iter(self.c.pending))
        self.assertIn("No real request was sent", self.c.handle(f"confirm {token}").text)

    def test_negative_free_cash_flow_recommends_nothing(self):
        c, _ = make_copilot("shop_c")
        text = c.handle("what financing fits").text
        self.assertIsNone(c.ctx.match["recommendation"])
        self.assertIn("nothing spare to repay from", text)


if __name__ == "__main__":
    unittest.main()
