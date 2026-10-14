"""Append-only audit log. Each entry carries the hash of the one before it, so edits show up."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_NUMBER = re.compile(r"\b\d{8,}\b")


def redact(value):
    """Mask e-mail addresses and long digit strings (account numbers) before anything is logged."""
    if isinstance(value, str):
        return _LONG_NUMBER.sub("[number removed]", _EMAIL.sub("[email removed]", value))
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _digest(prev: str, entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._seq, self._prev = 0, GENESIS
        if self.path.exists():
            lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                self._seq, self._prev = last["seq"], last["hash"]

    def append(self, actor: str, event: str, **data) -> dict:
        entry = {"seq": self._seq + 1, "ts": self.clock().isoformat(timespec="seconds"),
                 "actor": actor, "event": event, "data": redact(data)}
        digest = _digest(self._prev, entry)
        record = {**entry, "prev": self._prev, "hash": digest}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._seq, self._prev = record["seq"], digest
        return record

    def entries(self) -> list:
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    @staticmethod
    def verify(path) -> dict:
        """Check every link of the chain. Returns ok, number of entries and the first bad sequence number."""
        prev, n = GENESIS, 0
        p = Path(path)
        if not p.exists():
            return {"ok": True, "entries": 0, "first_bad_seq": None}
        for ln in p.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            rec = json.loads(ln)
            n += 1
            entry = {k: rec[k] for k in ("seq", "ts", "actor", "event", "data")}
            if rec.get("prev") != prev or rec.get("seq") != n or _digest(prev, entry) != rec.get("hash"):
                return {"ok": False, "entries": n, "first_bad_seq": rec.get("seq", n)}
            prev = rec["hash"]
        return {"ok": True, "entries": n, "first_bad_seq": None}
