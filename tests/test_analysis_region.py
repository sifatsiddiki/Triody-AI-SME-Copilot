"""Business analysis, the second country catalogue, and the forecast fixes found on shops C and D."""
import os
import tempfile
import unittest
from datetime import date, timedelta

from tests.helpers import ROOT, make_copilot
from triody import analysis, region
from triody.agent import Copilot
from triody.brain import _tool_calls_in_text
from triody.forecast import detect_recurring, fit_sales_model
from triody.ledger import Ledger, Txn


def shenzhen():
    d = tempfile.mkdtemp()
    return Copilot(Ledger.from_dir(ROOT / "data" / "shop_d"), audit_path=os.path.join(d, "a.jsonl"),
                   sandbox_dir=os.path.join(d, "sb"))


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.c, _ = make_copilot()
        self.a = self.c.run_tool("analyse_business", {}).data

    def test_monthly_rows_add_up(self):
        for r in self.a["months"]:
            self.assertAlmostEqual(r["gross_profit"], r["sales"] - r["variable_costs"], places=1)
            self.assertAlmostEqual(r["operating_profit"], r["gross_profit"] - r["fixed_costs"], places=1)

    def test_partial_months_are_marked(self):
        self.assertFalse(self.a["months"][0]["full"])
        self.assertFalse(self.a["months"][-1]["full"])
        self.assertTrue(all(r["full"] for r in self.a["months"][1:-1]))

    def test_month_on_month_uses_last_two_full_months(self):
        full = [r for r in self.a["months"] if r["full"]]
        self.assertEqual(self.a["month_on_month"]["month"], full[-1]["month"])
        self.assertEqual(self.a["month_on_month"]["previous"], full[-2]["month"])

    def test_biggest_supplier_is_named(self):
        self.assertIn("Lothian Mill and Dairy", self.a["narrative"])

    def test_breakeven_formula(self):
        b = self.a["breakeven"]
        expected = b["fixed_costs_per_day"] / (1 - b["variable_cost_ratio_pct"] / 100)
        self.assertAlmostEqual(b["daily_sales_needed"], expected, delta=1)

    def test_rule_planner_routes_margin_questions(self):
        reply = self.c.handle("How are my margins and costs?")
        self.assertIn("Break-even", reply.text)

    def test_every_number_in_the_narrative_is_grounded(self):
        from triody.agent import ToolResult, numbers_grounded
        r = ToolResult("analyse_business", True, self.a)
        self.assertEqual(numbers_grounded(self.a["narrative"], [r]), [])


class RegionTests(unittest.TestCase):
    def test_uk_shop_uses_uk_catalogue(self):
        c, _ = make_copilot()
        self.assertEqual(c.region["code"], "UK")
        self.assertIn("hmrc_ttp", {p["id"] for p in c.ctx.catalog})

    def test_shenzhen_shop_uses_china_catalogue_and_yuan(self):
        c = shenzhen()
        self.assertEqual(c.region["code"], "CN")
        self.assertEqual({p["id"] for p in c.ctx.catalog},
                         {"webank_wyd", "cn_inclusive_loan", "cn_tax_deferral", "cn_supplier_terms"})
        text = c.handle("Will I run out of cash?").text
        self.assertIn("¥", text)
        self.assertNotIn("£", text)

    def test_tax_bill_found_by_kind(self):
        c = shenzhen()
        self.assertGreater(c.ctx.tax_due(), 0)

    def test_tax_deferral_is_limited_to_the_tax_bill(self):
        c = shenzhen()
        r = c.run_tool("match_financing", {"amount": 50000, "term_months": 3})
        reasons = {x["id"]: x["reason"] for x in r.data["rejected"]}
        self.assertIn("cn_tax_deferral", reasons)
        self.assertIn("tax bill", reasons["cn_tax_deferral"])

    def test_bullet_repayment_is_one_payment(self):
        c = shenzhen()
        tax = c.ctx.tax_due()
        r = c.run_tool("match_financing", {"amount": tax, "term_months": 3, "urgency_days": 30})
        self.assertTrue(r.ok)
        plan = c.run_tool("check_plan", {"product_id": "cn_tax_deferral", "amount": tax, "term_months": 3})
        self.assertTrue(plan.ok, plan.error)
        self.assertEqual(plan.data["repayment"], "bullet")

    def test_limit_message_uses_the_local_currency(self):
        c = shenzhen()
        self.assertIn("¥", c.policy.check_limits(10_000_000).reason)

    def test_catalogue_name_cannot_escape_the_package(self):
        self.assertEqual(region.catalog_path("../../etc/passwd").parent, region.HERE)

    def test_every_china_product_lists_a_source_and_assumptions(self):
        import json
        data = json.loads(region.catalog_path("catalog_cn.json").read_text(encoding="utf-8"))
        for p in data["products"]:
            self.assertIn("checked", p["source"])
            self.assertTrue(p["source"]["assumed"], p["id"])


def _ledger(txns, as_of):
    return Ledger(txns, 1000.0, as_of)


class ForecastFixTests(unittest.TestCase):
    def test_monthly_bill_seen_three_times_is_projected(self):
        as_of = date(2026, 4, 20)
        txns = [Txn(date(2026, m, 1), "rent", "Landlord", -2000.0) for m in (2, 3, 4)]
        txns += [Txn(date(2026, 1, 20) + timedelta(days=i), "sales", "Till", 300.0) for i in range(91)]
        rec, _ = detect_recurring(_ledger(txns, as_of), as_of, 30)
        rent = [r for r in rec if r.category == "rent"]
        self.assertEqual(len(rent), 1)
        self.assertEqual(rent[0].dates[0], date(2026, 5, 1))

    def test_one_off_order_does_not_lift_the_level(self):
        as_of = date(2026, 3, 31)
        base = [Txn(as_of - timedelta(days=i), "sales", "Till", 400.0) for i in range(60)]
        spike = base + [Txn(as_of - timedelta(days=1), "sales", "Wedding order", 3000.0)]
        calm = fit_sales_model(_ledger(base, as_of), as_of).level
        lumpy = fit_sales_model(_ledger(spike, as_of), as_of).level
        self.assertLess(lumpy, calm * 1.10)

    def test_one_offs_stay_in_the_noise_pool(self):
        as_of = date(2026, 3, 31)
        txns = [Txn(as_of - timedelta(days=i), "sales", "Till", 400.0) for i in range(80)]
        txns.append(Txn(as_of - timedelta(days=50), "sales", "Wedding order", 3000.0))
        m = fit_sales_model(_ledger(txns, as_of), as_of)
        self.assertGreater(max(m.residuals), 3)


class TextToolCallTests(unittest.TestCase):
    def test_qwen_style_call_is_read(self):
        out = _tool_calls_in_text('<tool_call>{"name": "forecast_cash", "arguments": {}}</tool_call>',
                                  {"forecast_cash"})
        self.assertEqual(out[0][1], "forecast_cash")

    def test_unknown_tool_in_text_is_ignored(self):
        self.assertEqual(_tool_calls_in_text('{"name": "transfer_funds", "arguments": {}}', {"forecast_cash"}), [])

    def test_plain_text_is_not_a_call(self):
        self.assertEqual(_tool_calls_in_text("Your cash looks fine.", {"forecast_cash"}), [])

    def test_text_call_still_goes_through_the_policy_gate(self):
        from triody.brain import LLMBrain
        calls = iter([
            {"choices": [{"message": {"content": '{"name": "draft_financing_application", "arguments": '
                                                 '{"product_id": "hmrc_ttp", "amount": 5000, "term_months": 6}}'}}]},
            {"choices": [{"message": {"content": "Draft prepared."}}]},
        ])
        c, _ = make_copilot(brain=LLMBrain(provider="openai", transport=lambda p: next(calls)))
        reply = c.handle("apply for the tax plan")
        self.assertIn("P-001", reply.text)
        self.assertFalse((c.ctx.sandbox / "applications.json").exists())


if __name__ == "__main__":
    unittest.main()
