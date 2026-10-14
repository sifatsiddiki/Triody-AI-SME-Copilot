"""The audit anchoring tool, tested with a fake blockchain endpoint. No network is used."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT, make_copilot

spec = importlib.util.spec_from_file_location("anchor_audit", ROOT / "tools" / "anchor_audit.py")
anchor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anchor)


class FakeChain:
    """Answers the five RPC calls the tool makes and remembers the last transaction."""
    def __init__(self, chain_id=anchor.SEPOLIA_CHAIN_ID):
        self.chain_id, self.sent = chain_id, None

    def __call__(self, url, method, params):
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method in ("eth_getTransactionCount", "eth_gasPrice"):
            return "0x1"
        if method == "eth_estimateGas":
            return hex(22000)
        if method == "eth_sendRawTransaction":
            self.sent = params[0]
            return "0x" + "ab" * 32
        if method == "eth_getTransactionByHash":
            return {"input": self.data}
        raise AssertionError(method)


class FakeSigner:
    address = "0x" + "11" * 20

    def __call__(self, tx):
        FakeSigner.last = tx
        return "0xsigned"


class AnchorTests(unittest.TestCase):
    def setUp(self):
        self.c, self.tmp = make_copilot()
        self.c.handle("Will I run out of cash?")
        anchor.ANCHORS = Path(tempfile.mkdtemp()) / "anchors.jsonl"
        os.environ["SEPOLIA_RPC_URL"], os.environ["ANCHOR_PRIVATE_KEY"] = "http://fake", "0x" + "22" * 32

    def tearDown(self):
        os.environ.pop("SEPOLIA_RPC_URL", None)
        os.environ.pop("ANCHOR_PRIVATE_KEY", None)

    def test_send_writes_only_the_chain_head(self):
        chain = FakeChain()
        row = anchor.send(self.c.audit.path, call=chain, sign=FakeSigner())
        head = self.c.audit.entries()[-1]["hash"]
        self.assertEqual(FakeSigner.last["data"], anchor.payload(head))
        self.assertEqual(FakeSigner.last["value"], 0)
        self.assertEqual(FakeSigner.last["to"], FakeSigner.address)
        self.assertTrue(anchor.ANCHORS.exists())
        self.assertEqual(row["entries"], len(self.c.audit.entries()))

    def test_refuses_a_network_that_is_not_sepolia(self):
        with self.assertRaises(SystemExit):
            anchor.send(self.c.audit.path, call=FakeChain(chain_id=1), sign=FakeSigner())

    def test_verify_matches_then_detects_a_rewrite(self):
        chain = FakeChain()
        row = anchor.send(self.c.audit.path, call=chain, sign=FakeSigner())
        chain.data = FakeSigner.last["data"]
        self.c.handle("What financing fits?")      # later entries do not break the anchor
        self.assertTrue(anchor.verify(row["tx_hash"], self.c.audit.path, call=chain)["match"])
        lines = self.c.audit.path.read_text(encoding="utf-8").splitlines()
        self.c.audit.path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")   # drop the first entry
        self.assertFalse(anchor.verify(row["tx_hash"], self.c.audit.path, call=chain)["match"])

    def test_broken_chain_is_not_anchored(self):
        p = self.c.audit.path
        p.write_text(p.read_text(encoding="utf-8").replace("owner", "0wner", 1), encoding="utf-8")
        with self.assertRaises(SystemExit):
            anchor.chain_head(p)


if __name__ == "__main__":
    unittest.main()
