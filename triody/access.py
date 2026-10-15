"""Who may use the local dashboard.

The computer running Triody is trusted: a request from 127.0.0.1 needs no sign-in. Another device on
the same Wi-Fi (a phone or tablet) signs in once with a 6-digit pairing code that the dashboard shows
on the computer's screen. The code works once and expires after a few minutes. The device then gets a
random session cookie (HttpOnly, SameSite=Strict) and stays signed in until it signs out or the
session expires. No passwords exist, so none can leak or be reused.

Only SHA-256 hashes of session tokens are kept, in memory and optionally in a JSON file, so a copy of
that file cannot be replayed as a cookie.
"""
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

PAIRING_TTL_SECONDS = 600
SESSION_TTL_SECONDS = 30 * 24 * 3600
MAX_FAILURES_PER_CLIENT = 5          # wrong codes from one address before it must wait
LOCKOUT_SECONDS = 300
MAX_FAILURES_PER_CODE = 10           # wrong codes in total before the code is replaced
COOKIE = "triody_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Access:
    def __init__(self, store_path=None, clock=time.time):
        self.clock = clock
        self.store = Path(store_path) if store_path else None
        self._sessions = {}             # token hash -> {"expires": t, "device": label, "created": t}
        self._failures = {}             # client address -> (count, first failure time)
        self._code, self._code_expires, self._code_failures = None, 0.0, 0
        self._load()

    # -- persistence -------------------------------------------------------------------------
    def _load(self):
        if self.store and self.store.exists():
            try:
                data = json.loads(self.store.read_text(encoding="utf-8"))
                now = self.clock()
                self._sessions = {h: s for h, s in data.get("sessions", {}).items() if s.get("expires", 0) > now}
            except (ValueError, OSError):
                self._sessions = {}

    def _save(self):
        if not self.store:
            return
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sessions": self._sessions}, indent=1), encoding="utf-8")
        tmp.replace(self.store)

    # -- pairing code ------------------------------------------------------------------------
    def pairing_code(self) -> dict:
        """The current code, made fresh when the old one is used, expired or attacked."""
        now = self.clock()
        if self._code is None or now >= self._code_expires:
            self._code = f"{secrets.randbelow(10 ** 6):06d}"
            self._code_expires = now + PAIRING_TTL_SECONDS
            self._code_failures = 0
        return {"code": self._code, "expires_in": int(self._code_expires - now)}

    def _locked(self, client: str) -> int:
        count, since = self._failures.get(client, (0, 0.0))
        if count >= MAX_FAILURES_PER_CLIENT:
            wait = int(since + LOCKOUT_SECONDS - self.clock())
            if wait > 0:
                return wait
            self._failures.pop(client, None)
        return 0

    def pair(self, code: str, client: str, device: str = "") -> dict:
        """Check a pairing code. Returns {"ok": True, "token": ...} or {"ok": False, "error": ...}."""
        wait = self._locked(client)
        if wait:
            return {"ok": False, "error": f"Too many wrong codes. Try again in {wait // 60 + 1} minutes."}
        now = self.clock()
        code = "".join(ch for ch in str(code) if ch.isdigit())[:6]
        valid = self._code is not None and now < self._code_expires
        if not valid or not hmac.compare_digest(code, self._code or ""):
            count, since = self._failures.get(client, (0, now))
            self._failures[client] = (count + 1, since if count else now)
            self._code_failures += 1
            if self._code_failures >= MAX_FAILURES_PER_CODE:
                self._code = None          # replaced on the computer's screen
            return {"ok": False, "error": "That code is not right, or it has expired. Check the code on the "
                                          "computer and try again."}
        self._failures.pop(client, None)
        self._code = None                  # one use only
        token = secrets.token_urlsafe(32)
        self._sessions[_hash(token)] = {"expires": now + SESSION_TTL_SECONDS, "created": now,
                                        "device": "".join(ch for ch in device if ch.isprintable())[:60]}
        self._save()
        return {"ok": True, "token": token, "max_age": SESSION_TTL_SECONDS}

    # -- sessions ----------------------------------------------------------------------------
    def valid(self, token) -> bool:
        if not token or len(token) > 100:
            return False
        s = self._sessions.get(_hash(token))
        if s is None:
            return False
        if s["expires"] <= self.clock():
            self._sessions.pop(_hash(token), None)
            self._save()
            return False
        return True

    def sign_out(self, token) -> bool:
        if token and self._sessions.pop(_hash(token), None) is not None:
            self._save()
            return True
        return False

    def sign_out_all(self) -> int:
        n = len(self._sessions)
        self._sessions.clear()
        self._save()
        return n

    def devices(self) -> int:
        now = self.clock()
        return sum(1 for s in self._sessions.values() if s["expires"] > now)


def cookie_value(header: str):
    """Read our session token from a Cookie header."""
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None
