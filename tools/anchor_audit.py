"""Anchor the audit log outside this computer by writing its latest hash to a public test blockchain.

Why: the audit log is a hash chain. Anyone with file access could rewrite the whole file with a new,
valid chain (risk 2 in docs/SECURITY_ASSESSMENT.md). Once the latest hash sits in a public ledger
that nobody here controls, a rewrite shows up: the old hash on chain no longer matches the file.

Network: Ethereum Sepolia, a public test network. Test ETH has no money value. Only the hash leaves
the computer. It reveals nothing about the log's content.

Usage
    python tools/anchor_audit.py digest [log]          print and record the chain head, no network
    python tools/anchor_audit.py send [log]            write the head to Sepolia, record the tx hash
    python tools/anchor_audit.py verify <tx> [log]     check that the log still matches an anchor

`send` and `verify` need:
    pip install eth-account                            (only for send; the rest is standard library)
    SEPOLIA_RPC_URL     an RPC endpoint, for example from Alchemy or Infura (free tier)
    ANCHOR_PRIVATE_KEY  key of a test wallet holding a little Sepolia ETH from a faucet.
                        Use a fresh wallet that holds nothing else. Never commit this key.

Records go to evidence/anchors.jsonl, one line per anchor. Check a sent anchor on
https://sepolia.etherscan.io/tx/<tx hash>.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from triody.audit import AuditLog  # noqa: E402

SEPOLIA_CHAIN_ID = 11155111
DEFAULT_LOG = ROOT / "evidence" / "audit_log.jsonl"
ANCHORS = ROOT / "evidence" / "anchors.jsonl"
PREFIX = b"TRIODY1"          # marks the payload as ours, 7 bytes, followed by the 32-byte hash


def chain_head(log_path) -> dict:
    """The last hash of a verified chain, and how many entries it covers."""
    check = AuditLog.verify(log_path)
    if not check["ok"]:
        raise SystemExit(f"The audit chain is broken at entry {check['first_bad_seq']}. Not anchoring it.")
    entries = AuditLog(log_path).entries()
    if not entries:
        raise SystemExit("The audit log is empty.")
    return {"log": str(Path(log_path).relative_to(ROOT)) if Path(log_path).is_relative_to(ROOT) else str(log_path),
            "entries": len(entries), "head_hash": entries[-1]["hash"]}


def head_at(log_path, entries: int) -> str:
    """The chain hash after the first `entries` entries. Later entries do not change it."""
    rows = AuditLog(log_path).entries()
    if len(rows) < entries:
        raise SystemExit(f"The log has {len(rows)} entries, fewer than the {entries} that were anchored.")
    return rows[entries - 1]["hash"]


def payload(head_hash: str) -> str:
    return "0x" + PREFIX.hex() + head_hash


def record(row: dict):
    ANCHORS.parent.mkdir(parents=True, exist_ok=True)
    with open(ANCHORS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def rpc(url: str, method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read().decode())
    if "error" in out:
        raise SystemExit(f"RPC error from {method}: {out['error']}")
    return out["result"]


def send(log_path, call=rpc, sign=None) -> dict:
    url, key = os.environ.get("SEPOLIA_RPC_URL"), os.environ.get("ANCHOR_PRIVATE_KEY")
    if not url or not key:
        raise SystemExit("Set SEPOLIA_RPC_URL and ANCHOR_PRIVATE_KEY first. See the top of this file.")
    head = chain_head(log_path)
    if sign is None:
        try:
            from eth_account import Account
        except ImportError:
            raise SystemExit("Run: pip install eth-account") from None
        acct = Account.from_key(key)
        address = acct.address

        def sign(tx):
            signed = acct.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
            return "0x" + bytes(raw).hex().removeprefix("0x")
    else:
        address = sign.address
    if int(call(url, "eth_chainId", []), 16) != SEPOLIA_CHAIN_ID:
        raise SystemExit("That RPC endpoint is not Sepolia. Refusing to send.")
    data = payload(head["head_hash"])
    tx = {"to": address, "value": 0, "data": data, "chainId": SEPOLIA_CHAIN_ID,
          "nonce": int(call(url, "eth_getTransactionCount", [address, "pending"]), 16),
          "gasPrice": int(call(url, "eth_gasPrice", []), 16)}
    tx["gas"] = int(call(url, "eth_estimateGas", [{"from": address, "to": address, "data": data}]), 16)
    tx_hash = call(url, "eth_sendRawTransaction", [sign(tx)])
    row = {**head, "network": "sepolia", "tx_hash": tx_hash, "from": address,
           "anchored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "explorer": f"https://sepolia.etherscan.io/tx/{tx_hash}"}
    record(row)
    return row


def verify(tx_hash: str, log_path, call=rpc) -> dict:
    url = os.environ.get("SEPOLIA_RPC_URL")
    if not url:
        raise SystemExit("Set SEPOLIA_RPC_URL first.")
    tx = call(url, "eth_getTransactionByHash", [tx_hash])
    if not tx:
        raise SystemExit("Transaction not found.")
    data = (tx.get("input") or tx.get("data") or "").lower()
    if not data.startswith(payload("")):
        raise SystemExit("That transaction is not a Triody anchor.")
    anchored = data[len(payload("")):]
    rows = [json.loads(x) for x in ANCHORS.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if ANCHORS.exists() else []
    entries = next((r["entries"] for r in rows if r.get("tx_hash", "").lower() == tx_hash.lower()), None)
    if entries is None:
        raise SystemExit("No local record says how many entries this anchor covered.")
    chain_ok = AuditLog.verify(log_path)["ok"]
    now = head_at(log_path, entries)
    return {"tx_hash": tx_hash, "entries_covered": entries, "anchored_hash": anchored, "log_hash_now": now,
            "chain_ok": chain_ok, "match": chain_ok and now == anchored}


def main(argv):
    if not argv or argv[0] not in ("digest", "send", "verify"):
        print(__doc__)
        return 2
    if argv[0] == "digest":
        head = chain_head(argv[1] if len(argv) > 1 else DEFAULT_LOG)
        row = {**head, "network": None, "tx_hash": None,
               "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "payload": payload(head["head_hash"])}
        record(row)
        print(json.dumps(row, indent=2))
    elif argv[0] == "send":
        print(json.dumps(send(argv[1] if len(argv) > 1 else DEFAULT_LOG), indent=2))
    else:
        if len(argv) < 2:
            print("verify needs a transaction hash")
            return 2
        out = verify(argv[1], argv[2] if len(argv) > 2 else DEFAULT_LOG)
        print(json.dumps(out, indent=2))
        return 0 if out["match"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
