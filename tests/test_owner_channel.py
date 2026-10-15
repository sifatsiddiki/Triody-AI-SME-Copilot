"""The approval code travels on a separate channel. It must never appear in the chat, in a model
request, in the conversation memory of a model, or in the audit log."""
import json
import unittest

from tests.helpers import make_copilot
from triody import webapi


def _draft(c):
    c.handle("What financing fits?")
    return c.handle("apply")


class OwnerChannelTests(unittest.TestCase):
    def test_chat_reply_never_contains_the_code(self):
        c, _ = make_copilot()
        reply = _draft(c)
        code = c.owner_device[0]["code"]
        self.assertNotIn(code, reply.text)
        self.assertIn("owner device", reply.text)

    def test_daily_check_never_contains_the_code(self):
        c, _ = make_copilot()
        brief = c.briefing()
        self.assertTrue(c.owner_device)
        for m in c.owner_device:
            self.assertNotIn(m["code"], brief.text)

    def test_code_reaches_the_owner_device(self):
        c, _ = make_copilot()
        _draft(c)
        self.assertEqual(len(c.owner_device), 1)
        msg = c.owner_device[0]
        self.assertEqual(msg["proposal_id"], "P-001")
        self.assertIn(msg["code"], c.pending)

    def test_audit_log_never_holds_the_code(self):
        c, _ = make_copilot()
        _draft(c)
        code = c.owner_device[0]["code"]
        c.handle(f"confirm {code}")
        self.assertNotIn(code, c.audit.path.read_text(encoding="utf-8"))
        self.assertTrue(any(e["event"] == "confirmed" for e in c.audit.entries()))

    def test_model_never_sees_the_code(self):
        from triody.brain import LLMBrain
        sent = []
        script = iter([
            {"content": [{"type": "tool_use", "id": "1", "name": "match_financing", "input": {}}]},
            {"content": [{"type": "text", "text": "Here are your options."}]},
            {"content": [{"type": "tool_use", "id": "2", "name": "draft_financing_application",
                          "input": {"product_id": "hmrc_ttp", "amount": 5000, "term_months": 6}}]},
            {"content": [{"type": "text", "text": "Draft ready."}]},
            {"content": [{"type": "text", "text": "I do not have the code."}]},
        ])

        def transport(payload):
            sent.append(json.dumps(payload))
            return next(script)

        c, _ = make_copilot(brain=LLMBrain(transport=transport))
        c.handle("What financing fits?")
        c.handle("apply")
        c.handle("What is my approval code?")
        self.assertTrue(c.owner_device)
        code = c.owner_device[0]["code"]
        self.assertFalse(any(code in s for s in sent))

    def test_state_keeps_codes_out_of_pending(self):
        c, _ = make_copilot()
        _draft(c)
        status, body = webapi.dispatch(c, "GET", "/api/state")
        code = c.owner_device[0]["code"]
        self.assertNotIn(code, json.dumps(body["pending"]))
        self.assertEqual(body["owner_device"][0]["code"], code)

    def test_confirm_through_api_with_typed_code(self):
        c, _ = make_copilot()
        _draft(c)
        code = c.owner_device[0]["code"]
        status, body = webapi.dispatch(c, "POST", "/api/confirm", json.dumps({"code": code.upper()}))
        self.assertIn("Done", body["text"])
        self.assertEqual(body["state"]["owner_device"], [])

    def test_wrong_code_through_api_is_refused(self):
        c, _ = make_copilot()
        _draft(c)
        status, body = webapi.dispatch(c, "POST", "/api/confirm", json.dumps({"code": "000000000000"}))
        self.assertNotIn("Done", body["text"])
        self.assertEqual(len(c.pending), 1)

    def test_cancel_by_draft_number(self):
        c, _ = make_copilot()
        _draft(c)
        code = c.owner_device[0]["code"]
        self.assertIn("dropped", c.handle("cancel P-001").text)
        self.assertEqual(c.owner_device, [])
        self.assertNotIn("Done", c.handle(f"confirm {code}").text)

    def test_cancel_unknown_draft(self):
        c, _ = make_copilot()
        self.assertIn("no draft", c.handle("cancel P-009").text)


if __name__ == "__main__":
    unittest.main()
