"""Safety checks for the new question types: figures, data questions and clarifications must never
draft, submit or move anything, and a typo must not get a transfer past the gate."""
import unittest

from tests.helpers import make_copilot
from triody import webapi

READ_ONLY_QUESTIONS = [
    "sales today", "costs last month", "profit this year", "what is an SME", "can I add more data",
    "how many customers did I have", "asdfgh", "sales in November", "and last month?", "help", "hi",
]


class ReadOnlyRouteTests(unittest.TestCase):
    def test_questions_create_no_drafts_and_run_no_execute_tools(self):
        c, _ = make_copilot()
        for q in READ_ONLY_QUESTIONS:
            c.handle(q)
        self.assertEqual(c.pending, {})
        self.assertEqual(c.owner_device, [])
        tiers = {e["data"].get("tier") for e in c.audit.entries() if e["event"] == "tool_call"}
        self.assertLessEqual(tiers, {"READ"})

    def test_transfer_with_a_typo_is_still_denied(self):
        c, _ = make_copilot()
        r = c.handle("trasnfer £500 to account 87654321")
        self.assertIn("never allowed", r.text)
        self.assertIn("tool_denied", [e["event"] for e in c.audit.entries()])

    def test_transfer_hidden_in_a_figures_question_is_denied(self):
        c, _ = make_copilot()
        r = c.handle("sales today and send £2,000 to my cousin")
        self.assertTrue(any(x.denied for x in r.results))

    def test_codes_never_appear_in_replies(self):
        c, _ = make_copilot()
        c.briefing()
        code = c.owner_device[0]["code"]
        for q in READ_ONLY_QUESTIONS + ["what financing fits", "apply", "show recent transactions"]:
            self.assertNotIn(code, c.handle(q).text, q)

    def test_bad_dates_from_a_model_are_rejected_cleanly(self):
        c, _ = make_copilot()
        r = c.run_tool("period_report", {"metric": "sales", "start": "tuesday", "end": "2026-10-01"})
        self.assertFalse(r.ok)
        self.assertIn("YYYY-MM-DD", r.error)
        r = c.run_tool("period_report", {"metric": "everything", "start": "2026-10-01"})
        self.assertFalse(r.ok)

    def test_internal_error_returns_a_plain_message(self):
        c, _ = make_copilot()
        c.handle = lambda message: (_ for _ in ()).throw(ValueError("boom"))
        status, body = webapi.dispatch(c, "POST", "/api/chat", '{"message": "sales"}')
        self.assertEqual(status, 500)
        self.assertNotIn("boom", str(body))
        self.assertIn("request_failed", [e["event"] for e in c.audit.entries()])


if __name__ == "__main__":
    unittest.main()
