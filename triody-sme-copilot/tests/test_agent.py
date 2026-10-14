"""The agent's own behaviour: the daily check, plan testing, the language-model paths and fallback."""
import json
import unittest

from tests.helpers import ROOT, make_copilot
from triody import forecast as F
from triody.brain import LLMBrain
from triody.ledger import Ledger


class DailyCheckTests(unittest.TestCase):
    def test_check_finds_risk_and_prepares_one_draft(self):
        c, _ = make_copilot()
        reply = c.briefing()
        self.assertIn("Daily check", reply.text)
        self.assertEqual(len(c.pending), 1)
        self.assertFalse((c.ctx.sandbox / "applications.json").exists())  # nothing executed

    def test_check_runs_once_per_session(self):
        c, _ = make_copilot()
        c.briefing()
        c.briefing()
        self.assertEqual(len(c.pending), 1)

    def test_check_is_logged_as_agent_initiated(self):
        c, _ = make_copilot()
        c.briefing()
        events = [(e["actor"], e["event"]) for e in c.audit.entries()]
        self.assertIn(("agent", "daily_check_started"), events)
        self.assertIn(("agent", "daily_check_finished"), events)

    def test_owner_still_confirms_the_draft(self):
        c, _ = make_copilot()
        c.briefing()
        token = next(iter(c.pending))
        self.assertIn("Done", c.handle(f"confirm {token}").text)


class PlanTests(unittest.TestCase):
    def test_recommended_plan_lowers_the_risk(self):
        c, _ = make_copilot()
        c.handle("What financing fits?")
        rec, need = c.ctx.match["recommendation"], c.ctx.match["need"]
        r = c.run_tool("check_plan", {"product_id": rec["id"], "amount": need["amount"],
                                      "term_months": need["term_months"]})
        self.assertTrue(r.ok, r.error)
        self.assertLess(r.data["after"]["probability_negative_any_day"],
                        r.data["before"]["probability_negative_any_day"])

    def test_financing_reply_includes_the_tested_plan(self):
        c, _ = make_copilot()
        self.assertIn("Tested against the forecast", c.handle("What financing fits?").text)

    def test_plan_for_ineligible_product_is_refused(self):
        c, _ = make_copilot()
        r = c.run_tool("check_plan", {"product_id": "funding_circle", "amount": 4000, "term_months": 6})
        self.assertFalse(r.ok)


class LanguageModelTests(unittest.TestCase):
    def test_openai_compatible_tool_loop(self):
        steps = iter([
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "a", "type": "function",
                 "function": {"name": "forecast_cash", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"content": "Your balance today is £9,200."}}]},
        ])
        seen = []

        def transport(payload):
            seen.append(payload)
            return next(steps)

        c, _ = make_copilot(brain=LLMBrain(provider="openai", model="test", transport=transport))
        reply = c.handle("Will I run out of cash?")
        self.assertIn("9,200", reply.text)
        self.assertEqual(seen[0]["tools"][0]["type"], "function")
        self.assertEqual(seen[1]["messages"][-1]["role"], "tool")

    def test_openai_path_cannot_bypass_policy(self):
        steps = iter([
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {
                    "name": "transfer_funds", "arguments": json.dumps({"amount": 9000, "destination": "x"})}}]}}]},
            {"choices": [{"message": {"content": "Done."}}]},
        ])
        c, _ = make_copilot(brain=LLMBrain(provider="openai", model="test", transport=lambda p: next(steps)))
        c.handle("move my money")
        self.assertIn("tool_denied", [e["event"] for e in c.audit.entries()])

    def test_follow_up_questions_keep_context(self):
        payloads = []

        def transport(payload):
            payloads.append(payload)
            return {"content": [{"type": "text", "text": "Noted."}]}

        c, _ = make_copilot(brain=LLMBrain(provider="anthropic", model="test", transport=transport))
        c.handle("first question")
        c.handle("and what about next month?")
        self.assertEqual(payloads[-1]["messages"][0]["content"], "first question")

    def test_falls_back_to_rules_when_model_is_offline(self):
        def broken(payload):
            raise RuntimeError("model endpoint not reachable")

        c, _ = make_copilot(brain=LLMBrain(provider="anthropic", model="test", transport=broken))
        reply = c.handle("Will I run out of cash?")
        self.assertIn("rule-based planner answered", reply.text)
        self.assertIn("Cash alert", reply.text)
        self.assertIn("llm_unavailable", [e["event"] for e in c.audit.entries()])

    def test_model_never_sees_execute_tools(self):
        payloads = []
        c, _ = make_copilot(brain=LLMBrain(provider="anthropic", model="test",
                                           transport=lambda p: payloads.append(p) or {"content": []}))
        c.handle("hello")
        names = {t["name"] for t in payloads[0]["tools"]}
        self.assertFalse(names & {"submit_financing_application", "schedule_supplier_delay", "transfer_funds"})


class OtherShopsTests(unittest.TestCase):
    def test_closed_days_do_not_break_the_forecast(self):
        for shop in ("shop_b", "shop_c"):
            led = Ledger.from_dir(ROOT / "data" / shop)
            f = F.forecast(led, simulations=200)
            closed = [d for d in range(7) if F.fit_sales_model(led, led.as_of).weekday_index[d] == 0]
            self.assertEqual(len(closed), 1, shop)
            for row in f.days:
                self.assertLessEqual(row["p10"], row["p90"])

    def test_ranges_widen_for_a_lumpier_shop(self):
        a = F.forecast(Ledger.from_dir(ROOT / "data"), simulations=200).summary["level_uncertainty"]
        b = F.forecast(Ledger.from_dir(ROOT / "data" / "shop_b"), simulations=200).summary["level_uncertainty"]
        self.assertGreater(b, a)


if __name__ == "__main__":
    unittest.main()
