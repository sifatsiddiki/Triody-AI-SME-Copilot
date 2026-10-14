import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from triody.agent import Copilot  # noqa: E402
from triody.ledger import Ledger  # noqa: E402


def make_copilot(**kw):
    d = tempfile.mkdtemp()
    return Copilot(Ledger.from_dir(ROOT / "data"), audit_path=os.path.join(d, "audit.jsonl"),
                   sandbox_dir=os.path.join(d, "sandbox"), **kw), d
