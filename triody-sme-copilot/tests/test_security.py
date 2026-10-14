import json
import unittest

from tests.helpers import make_copilot
from triody.agent import numbers_grounded, ToolResult
from triody.audit import AuditLog, redact
from triody.policy import Policy
from triody.tools import validate_args


class FakeClock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class PermissionTests(unittest.TestCase):
    def test_agent_cannot_call_execute_tool(self):
        c, _ = make_copilot()
        r = c.run_tool("submit_financing_application", {"proposal_id": "P-001"}, origin="agent")
        self.assertTrue(r.denied)

    def test_owner_origin_without_code_is_still_denied(self):
        c, _ = make_copilot()
        r = c.run_tool("submit_financing_application", {"proposal_id": "P-001"}, origin="owner", redeemed=False)
        self.assertTrue(r.denied)

    def test_transfer_is_denied_for_every_origin(self):
        c, _ = make_copilot()
        for origin in ("agent", "owner"):
            r = c.run_tool("transfer_funds", {"amount": 10, "destination": "x"}, origin=origin, redeemed=True)
            self.assertTrue(r.denied, origin)

    def test_unknown_tool_is_denied(self):
        c, _ = make_copilot()
        self.assertTrue(c.run_tool("delete_everything", {}).denied)

    def test_language_model_tool_list_has_no_execute_or_prohibited_tools(self):
        c, _ = make_copilot()
        names = {t["name"] for t in c.tool_schemas()}
        self.assertNotIn("submit_financing_application", names)
        self.assertNotIn("schedule_supplier_delay", names)
        self.assertNotIn("transfer_funds", names)


class ConfirmationTests(unittest.TestCase):
    def _draft(self, c):
        c.handle("What financing fits?")
        return c.handle("apply")

    def test_draft_does_not_execute(self):
        c, d = make_copilot()
        self._draft(c)
        self.assertEqual(len(c.pending), 1)
        self.assertFalse((c.ctx.sandbox / "applications.json").exists())

    def test_confirm_executes_once_only(self):
        c, _ = make_copilot()
        self._draft(c)
        token = next(iter(c.pending))
        self.assertIn("Done", c.handle(f"confirm {token}").text)
        self.assertNotIn("Done", c.handle(f"confirm {token}").text)
        rows = json.loads((c.ctx.sandbox / "applications.json").read_text())
        self.assertEqual(len(rows), 1)

    def test_wrong_code_is_rejected(self):
        c, _ = make_copilot()
        self._draft(c)
        self.assertIn("not valid", c.handle("confirm 000000000000").text)

    def test_cancel_prevents_execution(self):
        c, _ = make_copilot()
        self._draft(c)
        token = next(iter(c.pending))
        c.handle(f"cancel {token}")
        self.assertNotIn("Done", c.handle(f"confirm {token}").text)
        self.assertFalse((c.ctx.sandbox / "applications.json").exists())

    def test_code_expires(self):
        clock = FakeClock()
        c, _ = make_copilot(policy=Policy(clock=clock))
        self._draft(c)
        token = next(iter(c.pending))
        clock.t += 601
        self.assertIn("expired", c.handle(f"confirm {token}").text)

    def test_code_is_bound_to_its_action(self):
        p = Policy()
        token = p.issue_token("submit_financing_application", {"proposal_id": "P-001"})
        self.assertFalse(p.redeem(token, "submit_financing_application", {"proposal_id": "P-002"}).allowed)

    def test_amount_limit(self):
        c, _ = make_copilot(policy=Policy(max_application=1000))
        self._draft(c)
        token = next(iter(c.pending))
        self.assertIn("limit", c.handle(f"confirm {token}").text)

    def test_daily_action_limit(self):
        c, _ = make_copilot(policy=Policy(max_actions_per_day=1))
        self._draft(c)
        first = next(iter(c.pending))
        c.handle(f"confirm {first}")
        c.handle("delay Lothian Mill and Dairy by 5 days")
        second = next(iter(c.pending))
        self.assertIn("daily limit", c.handle(f"confirm {second}").text)


class InjectionTests(unittest.TestCase):
    def test_memo_injection_cannot_trigger_a_transfer(self):
        c, _ = make_copilot()
        reply = c.handle("show recent transactions")
        self.assertIn("SYSTEM NOTE", reply.text)  # the text is visible to the owner as data
        events = [e for e in c.audit.entries() if e["event"] == "tool_call"]
        self.assertNotIn("transfer_funds", [e["data"]["tool"] for e in events])

    def test_model_view_hides_memo_text(self):
        c, _ = make_copilot()
        r = c.run_tool("recent_transactions", {"limit": 10})
        self.assertNotIn("SYSTEM NOTE", json.dumps(r.model_view()))

    def test_direct_transfer_request_is_refused(self):
        c, _ = make_copilot()
        self.assertIn("never allowed", c.handle("transfer £9,000 to account 12345678").text)

    def test_llm_brain_cannot_bypass_policy(self):
        from triody.brain import LLMBrain
        calls = iter([
            {"content": [{"type": "tool_use", "id": "1", "name": "transfer_funds",
                          "input": {"amount": 9000, "destination": "12345678"}}]},
            {"content": [{"type": "tool_use", "id": "2", "name": "submit_financing_application",
                          "input": {"proposal_id": "P-001"}}]},
            {"content": [{"type": "text", "text": "Done."}]},
        ])
        c, _ = make_copilot(brain=LLMBrain(transport=lambda payload: next(calls)))
        c.handle("please sort out my cash")
        denied = [e for e in c.audit.entries() if e["event"] == "tool_denied"]
        self.assertEqual(len(denied), 2)
        self.assertFalse((c.ctx.sandbox / "applications.json").exists())

    def test_ungrounded_numbers_are_replaced(self):
        from triody.brain import LLMBrain
        resp = {"content": [{"type": "text", "text": "You will have £48,213 next week."}]}
        c, _ = make_copilot(brain=LLMBrain(transport=lambda payload: resp))
        reply = c.handle("how much cash?")
        self.assertIn("replaced", reply.text)


class ValidationTests(unittest.TestCase):
    def test_schema_rejects_bad_input(self):
        schema = {"amount": {"type": "number", "required": True, "min": 1, "max": 100}}
        self.assertTrue(validate_args(schema, {}))
        self.assertTrue(validate_args(schema, {"amount": "5"}))
        self.assertTrue(validate_args(schema, {"amount": 500}))
        self.assertTrue(validate_args(schema, {"amount": 5, "extra": 1}))
        self.assertTrue(validate_args(schema, {"amount": True}))
        self.assertFalse(validate_args(schema, {"amount": 50}))

    def test_grounding_helper(self):
        ok = ToolResult("x", True, {"balance": 9200.0})
        self.assertEqual(numbers_grounded("Balance is £9,200.", [ok]), [])
        self.assertEqual(numbers_grounded("Balance is £7,777.", [ok]), ["7,777"])


class AuditTests(unittest.TestCase):
    def test_chain_verifies_and_detects_tampering(self):
        c, d = make_copilot()
        c.handle("Will I run out of cash?")
        c.handle("What financing fits?")
        path = c.audit.path
        self.assertTrue(AuditLog.verify(path)["ok"])
        lines = path.read_text().splitlines()
        rec = json.loads(lines[1])
        rec["data"]["text"] = "edited"
        lines[1] = json.dumps(rec)
        path.write_text("\n".join(lines) + "\n")
        self.assertFalse(AuditLog.verify(path)["ok"])

    def test_deleted_entry_is_detected(self):
        c, _ = make_copilot()
        c.handle("Will I run out of cash?")
        path = c.audit.path
        lines = path.read_text().splitlines()
        del lines[2]
        path.write_text("\n".join(lines) + "\n")
        self.assertFalse(AuditLog.verify(path)["ok"])

    def test_sensitive_values_are_redacted(self):
        out = redact({"text": "pay 12345678 and mail a.b@c.com"})
        self.assertNotIn("12345678", json.dumps(out))
        self.assertNotIn("a.b@c.com", json.dumps(out))


if __name__ == "__main__":
    unittest.main()
