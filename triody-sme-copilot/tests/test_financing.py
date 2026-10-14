import unittest

from tests.helpers import ROOT  # noqa: F401
from triody import financing as fin


def biz(**kw):
    base = dict(months_trading=30, annual_revenue=240000, credit_band="good", monthly_free_cash_flow=3000,
                card_share=0.8, receivables=0, collateral=False, supplier_relationship=True)
    base.update(kw)
    return fin.Business(**base)


class FinancingTests(unittest.TestCase):
    def setUp(self):
        self.catalog = fin.load_eval_catalog()

    def test_cost_of_credit_matches_known_loan_formula(self):
        p = next(x for x in self.catalog if x["id"] == "term_loan")
        c = fin.cost_of_credit({**p, "cost": {"model": "amortising", "apr": 12.0, "fee_pct": 0.0}}, 10000, 12)
        self.assertAlmostEqual(c["total_cost"], 661.85, places=1)  # standard 12% APR, 12 months

    def test_cash_advance_factor_rate(self):
        p = next(x for x in self.catalog if x["id"] == "mca")
        c = fin.cost_of_credit(p, 5000, 6)
        self.assertAlmostEqual(c["total_cost"], 1000.0, places=2)

    def test_hard_filters_give_reasons(self):
        res = fin.match(biz(), fin.Need(4000, 6, 10, "cash_gap"), self.catalog)
        rej = {r["id"]: r["reasons"] for r in res["rejected"]}
        self.assertIn("secured_loan", rej)
        self.assertTrue(any("minimum amount" in x for x in rej["secured_loan"]))

    def test_urgent_need_prefers_fast_product(self):
        res = fin.match(biz(), fin.Need(8000, 4, 3, "cash_gap"), self.catalog)
        self.assertEqual(res["recommendation"]["id"], "overdraft")

    def test_invoice_finance_for_slow_paying_customers(self):
        b = biz(months_trading=40, annual_revenue=600000, credit_band="fair", card_share=0.05, receivables=90000,
                monthly_free_cash_flow=5000)
        res = fin.match(b, fin.Need(40000, 3, 7, "cash_gap"), self.catalog)
        self.assertEqual(res["recommendation"]["id"], "invoice_fin")

    def test_unaffordable_product_is_not_recommended(self):
        b = biz(months_trading=15, annual_revenue=90000, credit_band="fair", monthly_free_cash_flow=300)
        res = fin.match(b, fin.Need(30000, 36, 30, "equipment"), self.catalog)
        self.assertIsNone(res["recommendation"])
        self.assertIsNotNone(res["note"])

    def test_negative_free_cash_flow_blocks_recommendation(self):
        res = fin.match(biz(monthly_free_cash_flow=-100), fin.Need(4000, 6, 10, "cash_gap"), self.catalog)
        self.assertIsNone(res["recommendation"])

    def test_no_eligible_product(self):
        b = biz(months_trading=5, annual_revenue=45000, credit_band="weak", monthly_free_cash_flow=700)
        res = fin.match(b, fin.Need(4000, 3, 3, "cash_gap"), self.catalog)
        self.assertIsNone(res["recommendation"])
        self.assertEqual(res["ranked"], [])

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(fin.WEIGHTS.values()), 1.0)

    def test_ranking_is_deterministic(self):
        a = fin.match(biz(), fin.Need(12000, 6, 10, "cash_gap"), self.catalog)
        b = fin.match(biz(), fin.Need(12000, 6, 10, "cash_gap"), self.catalog)
        self.assertEqual([r["id"] for r in a["ranked"]], [r["id"] for r in b["ranked"]])


class StockRuleTests(unittest.TestCase):
    """Stock is sold, and the sale repays most of the loan. Only short stock finance gets this treatment."""

    def setUp(self):
        self.catalog = fin.load_eval_catalog()

    def test_short_stock_burden_counts_only_unsold_share(self):
        p = next(x for x in self.catalog if x["id"] == "wc_loan")
        stock = fin.cost_of_credit(p, 12000, 6, purpose="stock")
        gap = fin.cost_of_credit(p, 12000, 6, purpose="cash_gap")
        self.assertAlmostEqual(stock["total_cost"], gap["total_cost"])
        self.assertAlmostEqual(stock["monthly_burden"],
                               (12000 * fin.STOCK_UNSOLD_SHARE + stock["total_cost"]) / 6, places=1)
        self.assertLess(stock["monthly_burden"], gap["monthly_burden"])

    def test_long_stock_loan_is_judged_normally(self):
        p = next(x for x in self.catalog if x["id"] == "wc_loan")
        stock = fin.cost_of_credit(p, 12000, 18, purpose="stock")
        gap = fin.cost_of_credit(p, 12000, 18, purpose="cash_gap")
        self.assertAlmostEqual(stock["monthly_burden"], gap["monthly_burden"])

    def test_stock_purchase_now_recommended_when_sales_repay_it(self):
        res = fin.match(biz(months_trading=20, annual_revenue=150000, credit_band="fair", card_share=0.2,
                            monthly_free_cash_flow=1800, supplier_relationship=False),
                        fin.Need(12000, 6, 14, "stock"), self.catalog)
        self.assertEqual(res["recommendation"]["id"], "wc_loan")


class LiveCatalogueTests(unittest.TestCase):
    """The live catalogue is built from public UK sources. Every product must say where its numbers come from."""

    def setUp(self):
        self.catalog = fin.load_catalog()

    def test_every_product_has_a_source_block(self):
        for p in self.catalog:
            src = p.get("source")
            self.assertIsNotNone(src, p["id"])
            self.assertEqual(src["checked"], "2026-10-14", p["id"])
            self.assertTrue(src["published"] or src["assumed"], p["id"])
            if src["url"] is None:
                self.assertEqual(src["published"], [], f"{p['id']} quotes figures with no source")

    def test_lloyds_overdraft_rate_is_bank_rate_plus_margin(self):
        p = next(x for x in self.catalog if x["id"] == "lloyds_overdraft")
        self.assertAlmostEqual(p["cost"]["apr"], 3.75 + 10.85)

    def test_overdraft_fee_tiers(self):
        p = next(x for x in self.catalog if x["id"] == "lloyds_overdraft")
        small = fin.cost_of_credit({**p, "cost": {**p["cost"], "apr": 0.0}}, 4000, 6)
        big = fin.cost_of_credit({**p, "cost": {**p["cost"], "apr": 0.0}}, 6000, 6)
        self.assertAlmostEqual(small["total_cost"], 12 * 6)          # £12 a month up to £5,000
        self.assertAlmostEqual(big["total_cost"], 150.0)            # 1.99% a year, minimum £150

    def test_tax_plan_needs_a_tax_bill(self):
        p = next(x for x in self.catalog if x["id"] == "hmrc_ttp")
        need = fin.Need(4000, 6, 20, "cash_gap")
        self.assertTrue(fin.check_eligibility(p, biz(tax_due=0.0), need))
        self.assertTrue(fin.check_eligibility(p, biz(tax_due=3000.0), need))
        self.assertEqual(fin.check_eligibility(p, biz(tax_due=6800.0), need), [])

    def test_existing_customer_rule(self):
        p = next(x for x in self.catalog if x["id"] == "lloyds_overdraft")
        need = fin.Need(4000, 6, 20, "cash_gap")
        self.assertTrue(any("Lloyds Bank customers" in r for r in fin.check_eligibility(p, biz(), need)))
        self.assertEqual(fin.check_eligibility(p, biz(existing_providers=("Lloyds Bank",)), need), [])

    def test_funding_circle_is_limited_companies_only(self):
        p = next(x for x in self.catalog if x["id"] == "funding_circle")
        need = fin.Need(20000, 24, 30, "growth")
        self.assertTrue(fin.check_eligibility(p, biz(legal_form="sole_trader"), need))
        self.assertEqual(fin.check_eligibility(p, biz(legal_form="ltd"), need), [])

    def test_market_rate_depends_on_credit_band(self):
        p = next(x for x in self.catalog if x["id"] == "asset_finance")
        good = fin.cost_of_credit(p, 20000, 36, "good")
        fair = fin.cost_of_credit(p, 20000, 36, "fair")
        self.assertLess(good["total_cost"], fair["total_cost"])


if __name__ == "__main__":
    unittest.main()
