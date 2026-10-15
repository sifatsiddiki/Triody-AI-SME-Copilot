"""Reads an owner's message before any tool runs.

Three steps, all offline and deterministic:
1. Clean the text and fix likely typos against a small finance vocabulary ("slaes" -> "sales").
2. Find the time period: today, yesterday, this week, this month, this year, a named month,
   "last 30 days", a quarter, or explicit dates. Periods are worked out against the last day in the
   books, not the computer clock, so a demo ledger gives the same answer on any day.
3. Pick one intent and, for figure questions, the metric the owner asked about.

The planner in brain.py turns the result into tool calls. Nothing here touches money or the ledger.
"""
import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

# ----------------------------------------------------------------------------- vocabulary
METRIC_WORDS = {
    "sales": {"sale", "sales", "sold", "sell", "selling", "sells", "revenue", "revenues", "turnover",
              "takings", "taking", "income", "made", "earned", "earn"},
    "costs": {"cost", "costs", "expense", "expenses", "expenditure", "spend", "spent", "spending",
              "outgoings", "outgoing", "bills", "outflow", "outflows"},
    "supplier": {"supplier", "suppliers", "purchases", "ingredients", "beans", "milk", "groceries"},
    "payroll": {"wage", "wages", "payroll", "salary", "salaries", "staff"},
    "rent": {"rent", "rental", "lease"},
    "utilities": {"utility", "utilities", "electricity", "energy", "gas"},
    "packaging": {"packaging"},
    "fees": {"fees", "fee", "charges"},
    "tax": {"tax", "taxes", "vat", "hmrc"},
    "loan": {"repayment", "repayments"},
    "profit": {"profit", "profits", "profitable", "margin", "margins", "loss", "losses"},
    "net_cash": {"net", "cashflow", "inflow", "inflows"},
    "transactions": {"transactions", "transaction", "payments", "entries"},
    "average": {"average", "avg", "typical"},
    "best_day": {"best", "busiest", "highest", "biggest", "top", "record"},
    "worst_day": {"worst", "slowest", "lowest", "quietest"},
}

UNSUPPORTED = {
    "customers": ("customer numbers", {"customer", "customers", "footfall", "visitors", "covers", "guests",
                                       "clients", "client"}),
    "stock": ("stock levels or inventory", {"inventory", "stocktake", "warehouse"}),
    "products": ("sales by product or menu item", {"product", "products", "item", "items", "menu", "sku",
                                                   "skus", "dish", "dishes", "coffee", "coffees"}),
    "staff": ("staff numbers or hours", {"employees", "employee", "headcount", "rota", "shifts", "hours"}),
    "refunds": ("refunds or chargebacks", {"refund", "refunds", "chargeback", "chargebacks", "returns"}),
    "reviews": ("reviews or website visits", {"review", "reviews", "rating", "ratings", "website", "traffic"}),
    "orders": ("order counts or basket sizes", {"orders", "basket", "baskets", "receipts", "tickets"}),
}

MONTHS = {}
for _i in range(1, 13):
    MONTHS[calendar.month_name[_i].lower()] = _i
    MONTHS[calendar.month_abbr[_i].lower()] = _i
MONTHS["sept"] = 9

NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "thirty": 30,
                "ninety": 90, "couple": 2, "few": 3}

PERIOD_WORDS = {"today", "todays", "yesterday", "yesterdays", "tomorrow", "week", "weeks", "weekly", "month",
                "months", "monthly", "year", "years", "yearly", "annual", "quarter", "quarters", "days", "day",
                "fortnight", "this", "last", "previous", "past", "next", "current", "ytd", "mtd", "wtd",
                "since", "between", "from", "until", "far", "date", "ever", "alltime"}

INTENT_WORDS = {"forecast", "shortfall", "cash", "balance", "enough", "afford", "alert", "run", "explain",
                "reason", "cause", "why", "loan", "loans", "finance", "financing", "fund", "funding", "borrow",
                "credit", "overdraft", "lend", "lender", "apply", "application", "delay", "postpone", "defer",
                "extend", "supplier", "recent", "ledger", "memo", "analysis", "analyse", "analyze", "trend",
                "trends", "breakeven", "performance", "help", "hello", "thanks", "data", "upload", "import",
                "connect", "information", "definition", "business", "businesses", "transfer", "predict",
                "projection", "future", "doing", "summary", "overview", "report", "statement", "money",
                "please", "show", "total", "much", "many", "afford", "hire", "hiring", "employ", "recruit",
                "improve", "increase", "reduce", "healthy", "health", "runway", "compare", "versus", "advice",
                "upcoming", "owe", "owed", "outstanding", "monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "sunday", "mondays", "tuesdays", "wednesdays", "thursdays", "fridays", "saturdays",
                "sundays", "weekday", "weekdays", "weekend", "weekends", "expensive", "machine", "equipment",
                "invest", "purchase", "margin", "gross", "operating", "depreciation", "insurance", "advance",
                "merchant", "working", "capital", "suspicious", "unusual", "christmas", "chart", "graph",
                "payroll", "summary", "summarise", "summarize", "trend", "growing", "declining", "profitable"}

# Common words that are never "corrected", so ordinary English does not turn into finance terms.
STOPWORDS = set("""
a an and are as at be been but by can could did do does doing done for from get got had has have how i if in
into is it its just like me my no not of on or our out over please same should so some than that the their
them then there these they this those to up us was we were what when where which who why will with would yes
you your about after again all also any back because before being both case cast cats come each even every
fine first give going good great here high just keep know last late less look lot made many more most much
need never new next nice now off once only other own part put really right said see seem show since still
such sure take tell than thing think time told too try turn under very want way well went whole work year
okay ok hi hey hiya thank thanks cheers bye morning afternoon evening sorry save saving savings safe send
rest feel feed pass load list test speed change changed changes cheap mean means meant shop shops able
renewal renew renewed overdrawn owes worried worry stressed scared anxious plan planning budget open opened
weather whether rather father mother water later make makes making makings maker lose losing lost joke jokes
""".split())

GREETINGS = {"hi", "hello", "hey", "hiya", "morning", "afternoon", "evening", "yo", "greetings", "howdy"}
THANKS = {"thanks", "thank", "cheers", "ta", "thx", "great", "perfect", "ok", "okay", "cool", "nice"}


def _vocabulary() -> set:
    words = set(MONTHS) | PERIOD_WORDS | INTENT_WORDS
    for group in METRIC_WORDS.values():
        words |= group
    for _, group in UNSUPPORTED.values():
        words |= group
    return {w for w in words if len(w) >= 4}


VOCAB = _vocabulary()


def edit_distance(a: str, b: str, limit: int = 3) -> int:
    """Optimal string alignment distance: insert, delete, substitute, or swap two neighbours."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def correct(token: str) -> str:
    """Return the closest vocabulary word for a likely typo, or the token unchanged."""
    if token in VOCAB or token in STOPWORDS or len(token) < 4 or not token.isalpha():
        return token
    allowed = 1 if len(token) <= 6 else 2
    best, best_key = token, (allowed + 1, 0)
    for word in sorted(VOCAB):
        if word[0] != token[0] and not (len(token) > 1 and word[:2] == token[1::-1]):
            continue
        key = (edit_distance(token, word, allowed), abs(len(word) - len(token)))
        if key < best_key:
            best, best_key = word, key
    return best if best_key[0] <= allowed else token


def normalise(text: str, keep=()):
    """Lower-case, drop apostrophes and punctuation, fix typos. Words in `keep` (for example the names of
    the shop's payees) are never "fixed". Returns (tokens, corrections)."""
    t = text.lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"\b(when|what|where|who|how)'?s\b", r"\1 is", t)        # when's, whens -> when is
    t = re.sub(r"\b(i|we|you|they)'m\b", r"\1 am", t)
    t = re.sub(r"(\w)'s\b", r"\1", t)              # today's -> today, shop's -> shop
    t = t.replace("'", "")
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)  # 1st -> 1
    t = re.sub(r"[^a-z0-9£$¥%/\-. ]+", " ", t)
    raw = [w.strip(".") for w in t.split() if w.strip(".")]
    tokens, fixes = [], []
    for w in raw:
        c = w if w in keep else correct(w)
        if c != w:
            fixes.append((w, c))
        tokens.append(c)
    return tokens, fixes


# ----------------------------------------------------------------------------- periods
@dataclass
class Period:
    start: date
    end: date
    label: str
    kind: str                       # day, week, month, quarter, year, rolling, range, all
    to_date: bool = False           # "this month so far" and similar
    compare_start: date = None
    compare_end: date = None
    compare_label: str = ""

    def args(self) -> dict:
        out = {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label}
        if self.compare_start:
            out.update(compare_start=self.compare_start.isoformat(), compare_end=self.compare_end.isoformat(),
                       compare_label=self.compare_label)
        return out


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_months(y: int, m: int, k: int):
    n = y * 12 + (m - 1) + k
    return n // 12, n % 12 + 1


def fmt_day(d: date) -> str:
    return f"{d.day} {calendar.month_abbr[d.month]} {d.year}"


def _month_period(y: int, m: int, today: date) -> Period:
    start, end = date(y, m, 1), _month_end(y, m)
    py, pm = _add_months(y, m, -1)
    label = f"{calendar.month_name[m]} {y}"
    if start <= today < end:
        # month still running: compare with the same days of the month before
        cmp_end_day = min(today.day, calendar.monthrange(py, pm)[1])
        return Period(start, today, f"{label} so far", "month", True, date(py, pm, 1), date(py, pm, cmp_end_day),
                      f"the same point in {calendar.month_name[pm]}")
    return Period(start, end, label, "month", False, date(py, pm, 1), _month_end(py, pm),
                  f"{calendar.month_name[pm]} {py}")


def _quarter_period(y: int, q: int, today: date, label=None) -> Period:
    start = date(y, 3 * q - 2, 1)
    end = _month_end(y, 3 * q)
    label = label or f"Q{q} {y}"
    if start <= today < end:
        return Period(start, today, f"{label} so far", "quarter", True)
    return Period(start, end, label, "quarter")


def _year_period(y: int, today: date, label=None) -> Period:
    start, end = date(y, 1, 1), date(y, 12, 31)
    label = label or str(y)
    if start <= today < end:
        return Period(start, today, f"{label} so far", "year", True)
    return Period(start, end, label, "year")


_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})\s+([a-z]{3,9})(?:\s+(\d{4}))?\b"), "d_month"),
    (re.compile(r"\b([a-z]{3,9})\s+(\d{1,2})(?:\s+(\d{4}))?\b(?!\s*(?:days?|weeks?|months?))"), "month_d"),
]


def _explicit_dates(text: str, today: date) -> list:
    """Find calendar dates such as 2026-10-01, 01/10/2026, 1 October, October 1. Returns (pos, date)."""
    found = []
    for rx, kind in _DATE_PATTERNS:
        for m in rx.finditer(text):
            try:
                if kind == "ymd":
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                elif kind == "dmy":
                    y = int(m.group(3))
                    d = date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
                elif kind == "d_month":
                    if m.group(2) not in MONTHS:
                        continue
                    d = date(int(m.group(3) or today.year), MONTHS[m.group(2)], int(m.group(1)))
                else:
                    if m.group(1) not in MONTHS or int(m.group(2)) > 31:
                        continue
                    if m.group(3) is None and len(m.group(2)) == 4:
                        continue
                    d = date(int(m.group(3) or today.year), MONTHS[m.group(1)], int(m.group(2)))
            except ValueError:
                continue
            if not any(abs(p - m.start()) < 3 for p, _ in found):
                found.append((m.start(), d))
    return sorted(found)


def parse_period(tokens: list, today: date, first_day: date = None) -> Period:
    """Work out the period a message talks about, or None if it names none."""
    text = " ".join(tokens)
    has = lambda *ws: any(re.search(rf"\b{w}\b", text) for w in ws)

    dates = _explicit_dates(text, today)
    if len(dates) >= 2:
        a, b = sorted([dates[0][1], dates[1][1]])
        return Period(a, b, f"{fmt_day(a)} to {fmt_day(b)}", "range")
    if len(dates) == 1:
        d = dates[0][1]
        if has("since", "from") and not has("to", "until"):
            return Period(d, today, f"since {fmt_day(d)}", "range", True)
        return Period(d, d, fmt_day(d), "day", False, d - timedelta(days=7), d - timedelta(days=7),
                      "the same day a week earlier")

    if has("all time", "alltime", "ever", "since the start", "since start", "whole history", "all history"):
        start = first_day or today
        return Period(start, today, "all of your history", "all")

    if has("today", "todays", "tonight", "so far today"):
        prev = today - timedelta(days=7)
        return Period(today, today, f"today ({calendar.day_name[today.weekday()]} {fmt_day(today)})", "day",
                      False, prev, prev, f"the same day last week ({fmt_day(prev)})")
    if has("yesterday", "yesterdays"):
        d = today - timedelta(days=1)
        prev = d - timedelta(days=7)
        return Period(d, d, f"yesterday ({calendar.day_name[d.weekday()]} {fmt_day(d)})", "day", False,
                      prev, prev, f"the same day the week before ({fmt_day(prev)})")
    single = re.search(r"\b(on|last|this past|next|this coming|coming) (monday|tuesday|wednesday|thursday|friday|"
                       r"saturday|sunday)\b", text)
    if single:
        target = list(calendar.day_name).index(single.group(2).capitalize())
        if single.group(1) in ("next", "this coming", "coming"):
            day = today + timedelta(days=(target - today.weekday()) % 7 or 7)
        else:
            back = (today.weekday() - target) % 7
            day = today - timedelta(days=back if back or single.group(1) == "on" else 7)
        label = f"{calendar.day_name[day.weekday()]} {fmt_day(day)}"
        return Period(day, day, label, "day")
    if has("tomorrow"):
        d = today + timedelta(days=1)
        return Period(d, d, f"tomorrow ({fmt_day(d)})", "day")

    # rolling windows: last 7 days, past two weeks, previous 3 months, last fortnight
    m = re.search(r"\b(last|past|previous|recent)\s+(\d{1,3}|[a-z]+)\s+(days?|weeks?|months?)\b", text)
    if m:
        n = int(m.group(2)) if m.group(2).isdigit() else NUMBER_WORDS.get(m.group(2))
        if n:
            unit = m.group(3).rstrip("s")
            days = n * {"day": 1, "week": 7, "month": 30}[unit]
            days = max(1, min(days, 3660))
            start = today - timedelta(days=days - 1)
            prev_end = start - timedelta(days=1)
            return Period(start, today, f"the last {n} {unit}{'s' if n != 1 else ''}", "rolling", False,
                          prev_end - timedelta(days=days - 1), prev_end, f"the {n} {unit}{'s' if n != 1 else ''} before")
    m = re.search(r"\bnext\s+(\d{1,3}|[a-z]+)\s+(days?|weeks?|months?)\b", text)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMBER_WORDS.get(m.group(1), 1)
        days = n * {"day": 1, "week": 7, "month": 30}[m.group(2).rstrip("s")]
        return Period(today + timedelta(days=1), today + timedelta(days=days), f"the next {m.group(1)} "
                      f"{m.group(2)}", "rolling")
    if has("last fortnight", "past fortnight"):
        start = today - timedelta(days=13)
        return Period(start, today, "the last two weeks", "rolling", False, start - timedelta(days=14),
                      start - timedelta(days=1), "the two weeks before")

    monday = today - timedelta(days=today.weekday())
    if has("this week", "week so far", "week to date", "wtd", "current week"):
        days = (today - monday).days
        return Period(monday, today, "this week so far", "week", True, monday - timedelta(days=7),
                      monday - timedelta(days=7) + timedelta(days=days), "the same days last week")
    if has("last week", "previous week", "past week"):
        s = monday - timedelta(days=7)
        return Period(s, s + timedelta(days=6), f"last week ({fmt_day(s)} to {fmt_day(s + timedelta(days=6))})",
                      "week", False, s - timedelta(days=7), s - timedelta(days=1), "the week before")
    if has("next week"):
        s = monday + timedelta(days=7)
        return Period(s, s + timedelta(days=6), "next week", "week")

    if has("this month", "month so far", "month to date", "mtd", "current month"):
        return _month_period(today.year, today.month, today)
    if has("last month", "previous month", "past month"):
        y, mo = _add_months(today.year, today.month, -1)
        return _month_period(y, mo, today)
    if has("next month"):
        y, mo = _add_months(today.year, today.month, 1)
        return _month_period(y, mo, today)

    qm = re.search(r"\bq([1-4])\b(?:\s+(\d{4}))?", text)
    if qm:
        return _quarter_period(int(qm.group(2) or today.year), int(qm.group(1)), today)
    cq = (today.month - 1) // 3 + 1
    if has("this quarter", "current quarter", "quarter so far"):
        return _quarter_period(today.year, cq, today, "this quarter")
    if has("last quarter", "previous quarter"):
        y, q = (today.year, cq - 1) if cq > 1 else (today.year - 1, 4)
        return _quarter_period(y, q, today, f"last quarter (Q{q} {y})")

    if has("this year", "year so far", "year to date", "ytd", "current year", "this yr", "so far this year"):
        return _year_period(today.year, today, "this year")
    if has("last year", "previous year", "past year"):
        return _year_period(today.year - 1, today, f"last year ({today.year - 1})")
    if has("next year"):
        return _year_period(today.year + 1, today, f"next year ({today.year + 1})")

    # a month by name, with or without a year: "october", "sales in sept 2026"
    for i, tok in enumerate(tokens):
        if tok not in MONTHS:
            continue
        if tok == "may":   # also a verb: only a month after a preposition, before a year, or at the end
            before = tokens[i - 1] if i else ""
            after = tokens[i + 1] if i + 1 < len(tokens) else ""
            if not (before in ("in", "for", "during", "of", "since", "through", "throughout")
                    or re.fullmatch(r"\d{4}", after) or i == len(tokens) - 1):
                continue
        if tok in ("mar", "march") and i + 1 < len(tokens) and not re.fullmatch(r"\d{4}", tokens[i + 1]) \
                and tokens[i - 1:i] not in (["in"], ["for"], ["of"], ["since"], ["during"]) and i != len(tokens) - 1:
            continue
        year = int(tokens[i + 1]) if i + 1 < len(tokens) and re.fullmatch(r"\d{4}", tokens[i + 1]) else today.year
        p = _month_period(year, MONTHS[tok], today)
        if i and tokens[i - 1] == "since":
            return Period(p.start, today, f"since {calendar.month_name[MONTHS[tok]]} {year}", "range", True)
        return p

    ym = re.search(r"\b(?:in|for|during|of)\s+(20\d{2})\b", text) or re.fullmatch(r".*\b(20\d{2})\b.*", text)
    if ym:
        return _year_period(int(ym.group(1)), today)
    return None


# ----------------------------------------------------------------------------- intents
@dataclass
class Parsed:
    intent: str
    metric: str = None
    period: Period = None
    tokens: list = field(default_factory=list)
    corrections: list = field(default_factory=list)
    unsupported: str = None
    supplier: str = None
    days: int = None
    amount: float = None            # what-if: a one-off amount
    monthly: float = None           # what-if: a cost (or a saving, negative) every month
    sales_pct: float = None         # what-if: change in sales, percent
    label: str = ""                 # what-if: what the money is for
    weekday: int = None             # 0 = Monday
    term: str = None                # glossary entry
    compare: Period = None          # second period in "compare X to Y"
    suggestions: list = field(default_factory=list)


FINANCE_RX = re.compile(r"\b(loans?|financ\w*|funding|fund|borrow\w*|credit|overdraft|lend\w*|time to pay|"
                        r"cash advance|grant|grants|invoice finance|interest)\b")
TRANSFER_RX = re.compile(r"\b(transfer|wire|send|move)\b.{0,30}(\d|money|funds|cash)")
PAY_RX = re.compile(r"\bpay\b.{0,20}\d")


def _metric(tokens: list, text: str):
    """The metric a message asks about, or None."""
    if re.search(r"\bhow much (money )?(did|have|do|will) (i|we) (make|made|take|taken|earn|earned|bring in|brought in)\b",
                 text):
        return "sales"
    if re.search(r"\bhow much (did|does|has|have)\b.{0,25}\b(take|took|taken|make|made|earn|earned|bring in|brought in)\b",
                 text) and not re.search(r"\b(cost|spend|spent|pay|paid)\b", text):
        return "sales"
    if re.search(r"\b(how many|number of|count)\b.{0,20}\b(transactions?|payments|entries)\b", text):
        return "transactions"
    if re.search(r"\bcash ?flow\b|\bmoney (in and out|in|out)\b|\bin and out\b|\bnet cash\b", text):
        return "net_cash"
    if re.search(r"\bloan repayments?\b|\brepayments? on (the|my) loan\b", text):
        return "loan"
    if re.search(r"\bbank (fees|charges)\b|\bcard (fees|charges)\b", text):
        return "fees"
    words = set(tokens)
    found = [k for k, group in METRIC_WORDS.items() if words & group]
    if "best_day" in found or "worst_day" in found:
        if words & ({"day", "days", "date"} | METRIC_WORDS["sales"]):
            return "best_day" if "best_day" in found else "worst_day"
        found = [f for f in found if f not in ("best_day", "worst_day")]
    if "average" in found:
        return "average"
    for k in ("supplier", "payroll", "rent", "utilities", "packaging", "fees", "tax", "loan"):
        if k in found and ("costs" in found or "how much" in text or "spent" in words or "paid" in words
                           or "pay" in words or len(found) == 1 or "sales" not in found):
            return k
    for k in ("profit", "net_cash", "costs", "sales", "transactions"):
        if k in found:
            return k
    return None


WEEKDAYS = {name: i for i, name in enumerate(calendar.day_name)}
WEEKDAYS.update({f"{name}s": i for name, i in list(WEEKDAYS.items())})
WEEKDAYS = {k.lower(): v for k, v in WEEKDAYS.items()}

GLOSSARY_EXAMPLES = {"cash flow": "cash flow this month", "gross margin": "gross margin last month",
                     "operating profit": "profit last month", "net profit": "profit this year",
                     "break even": "what's my break-even?", "free cash flow": "is my business healthy?",
                     "runway": "how long will my cash last?", "vat": "when is my next VAT bill?",
                     "time to pay": "what financing fits?", "buffer": "will I run out of cash?",
                     "overdraft": "what financing fits?", "invoice finance": "what financing fits?",
                     "merchant cash advance": "what financing fits?", "apr": "what financing fits?",
                     "working capital": "what bills are coming up?"}

GLOSSARY = {
    "cash flow": ("cash flow", "Cash flow is the money that comes in (sales takings) minus the money that goes out "
                               "(bills, wages, rent, tax) over a period. A business can make a profit on paper and "
                               "still run out of cash if bills land before the takings."),
    "gross margin": ("gross margin", "Gross margin is sales minus the direct cost of what you sell (for you, "
                                     "supplier and packaging bills), as a share of sales. It shows how much of each "
                                     "pound of sales is left to pay wages, rent and everything else."),
    "operating profit": ("operating profit", "Operating profit is gross profit minus running costs: wages, rent, "
                                             "utilities, loan repayments and fees. Tax is shown apart."),
    "net profit": ("net profit", "Net profit is what is left after every cost, including tax and interest. I show "
                                 "operating profit and tax paid separately, because the books do not hold "
                                 "depreciation or accruals."),
    "break even": ("break-even", "Break-even is the sales you need to cover your fixed costs, after the variable "
                                 "costs that rise with sales. Below it you lose money, above it you make money."),
    "free cash flow": ("free cash flow", "Free cash flow here is the average money left each month after all "
                                         "payments, over the last six months. It is what a loan repayment would "
                                         "have to come out of."),
    "runway": ("runway", "Runway is how long your cash lasts before it runs out at the current rate."),
    "vat": ("VAT", "VAT is a tax you add to most sales and pay to HMRC, usually every quarter, after taking off the "
                   "VAT you paid on your own purchases. It is not your money, so it helps to put it aside."),
    "time to pay": ("Time to Pay", "Time to Pay is an HMRC payment plan. If you cannot pay a tax bill in full, HMRC "
                                   "may let you pay it in monthly instalments, with interest. It is not a loan and "
                                   "no money is paid to you."),
    "overdraft": ("overdraft", "An overdraft lets your bank balance go below zero up to an agreed limit. You pay "
                               "interest on what you use, and often a fee. Banks can withdraw it at short notice."),
    "invoice finance": ("invoice finance", "Invoice finance lends you money against invoices your customers have "
                                           "not paid yet. It suits businesses that sell on credit, not shops paid "
                                           "at the till."),
    "merchant cash advance": ("merchant cash advance", "A merchant cash advance is a lump sum repaid as a share of "
                                                       "your future card sales. It is quick but usually expensive."),
    "buffer": ("safety buffer", "The safety buffer is the lowest balance you want to keep for surprises. Triody "
                                "warns when the chance of falling under it gets high."),
    "apr": ("APR", "APR is the yearly cost of borrowing, including interest and most fees, as a percentage."),
    "working capital": ("working capital", "Working capital is the money tied up in running the business day to "
                                           "day: cash, stock and money owed to you, minus bills you owe."),
}

EXAMPLES = {
    "Sales today": "report", "Sales this month": "report", "Costs last month": "report",
    "Profit this year": "report", "Will I run out of cash?": "forecast", "Why is cash tight?": "explain",
    "What bills are coming up?": "upcoming", "Is my business healthy?": "health",
    "How can I improve cash flow?": "advice", "What financing fits?": "financing",
    "Which supplier costs the most?": "top", "Sales by day of the week": "weekdays",
    "Can I afford £3,000 for a new machine?": "what_if", "What if sales drop 10%?": "what_if",
    "Compare this month to last month": "compare", "Show recent transactions": "recent",
    "How are my margins and costs?": "analysis", "What is gross margin?": "glossary",
}

_MONEY = r"(?:£|\$|¥|gbp\s?|rmb\s?|cny\s?)?\s?(\d[\d,]*(?:\.\d+)?)\s?(k|grand|thousand)?\b"


def _money(text: str):
    """The first amount of money in a message, or None. '3k', '£3,000' and '3 grand' all read 3000."""
    for m in re.finditer(_MONEY, text):
        whole = m.group(0)
        if not re.search(r"[£$¥]|gbp|rmb|cny|k|grand|thousand", whole) and float(m.group(1).replace(",", "")) < 50:
            continue                                     # small bare numbers are days, months or percents
        if re.match(r"\s*\d{4}\s*$", whole) and 1990 <= int(m.group(1)) <= 2100:
            continue                                     # a year
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= 1000
        return v
    return None


def _split_compare(text: str):
    """'compare this month to last month' -> ('this month', 'last month'); otherwise None."""
    m = re.search(r"\b(?:vs|versus|compared (?:to|with)|against)\b", text)
    if m:
        return text[:m.start()], text[m.end():]
    m = re.search(r"\bcompare\b(.*?)\b(?:to|with|and)\b(.*)$", text)
    if m:
        return m.group(1), m.group(2)
    return None


def _what_if(text: str, lower: str):
    """Read a spending or sales question. Returns a dict or None."""
    pct = re.search(r"(\d{1,3}(?:\.\d+)?)\s?(?:%|percent|per cent)", text)
    lose = re.search(r"\b(lose|lost|losing)\b\D{0,20}(\d{1,3}(?:\.\d+)?)\s?(?:%|percent|per cent)\s*(?:of\s+)?(?:my\s+|our\s+)?"
                     r"(sales|takings|revenue|turnover|customers|business)", text)
    if lose:
        return {"sales_pct": -float(lose.group(2)), "label": "sales"}
    sales_move = re.search(r"\b(sales|takings|revenue|turnover|prices?)\b.{0,25}\b(drop|drops|fall|falls|down|"
                           r"decrease|decreases|go down|goes down|lower|cut|rise|rises|up|increase|increases|grow|"
                           r"grows|raise|go up|goes up|higher)\b|\b(raise|increase|cut|lower|drop)\b.{0,10}\b(prices?)\b", text)
    if pct and sales_move:
        down = re.search(r"\b(drop|drops|fall|falls|down|decrease|decreases|lower|cut)\b", text) and \
            not re.search(r"\b(raise|rise|rises|increase|increases|up|grow|grows|higher)\b", text)
        v = float(pct.group(1))
        return {"sales_pct": -v if down else v, "label": "prices" if "price" in text else "sales"}
    amount = _money(lower)
    spend = re.search(r"\b(afford|buy|spend|spending|purchase|invest|hire|hiring|employ|recruit|pay for|get a|"
                      r"take on|what if|lease|upgrade|replace|refit|cost me|costs? me|take|raise|increase|pay rise|"
                      r"bonus|salary)\b", text)
    if amount is None or not spend:
        saving = re.search(r"\b(save|cut|reduce|lower)\b.{0,30}\b(costs?|spend|bills?|wages?)\b", text)
        if amount is not None and saving:
            monthly = re.search(r"\b(a|per|each|every) (month|mo)\b|\bmonthly\b", text)
            return {"monthly": -amount if monthly else None, "amount": None if monthly else -amount,
                    "label": "a cost saving"}
        return None
    monthly = re.search(r"\b(a|per|each|every) (month|mo)\b|\bmonthly\b|\b(salary|wage|hire|hiring|employ|"
                        r"recruit|rent|lease|subscription)\b", text)
    weekly = re.search(r"\b(a|per|each|every) week\b|\bweekly\b", text)
    yearly = re.search(r"\b(a|per|each|every) (year|annum)\b|\byearly\b|\bannual\b|\bsalary\b", text)
    what = re.search(r"\b(?:for|on|buy|get|hire|purchase|afford)\s+(?:a|an|another|the|new|some|my)?\s*"
                     r"((?:new |second |extra |another )?[a-z][a-z ]{2,30}?)(?=\s+(?:for|at|costing|which|that|if|"
                     r"in|next|this|£)|[?.!]|$)", text)
    label = what.group(1).strip() if what else "this"
    label = re.sub(r"^((to|buy|get|spend|a|an|the|some|on|my)\s+)+", "", label)
    label = re.sub(r"\s+(worth|costing|for|at|is|that|which)$", "", label).strip()
    if re.fullmatch(r"(it|this|that|one|£?\d.*)", label or ""):
        label = "this"
    if yearly and (re.search(r"\bsalary\b", text) or yearly) and not re.search(r"\b(a|per) month\b", text):
        if amount >= 6000:
            return {"monthly": round(amount / 12, 2), "label": label, "yearly": amount}
    if weekly:
        return {"monthly": round(amount * 52 / 12, 2), "label": label}
    if monthly:
        return {"monthly": amount, "label": label}
    return {"amount": amount, "label": label}


def _bill_focus(text: str, suppliers=()) -> str:
    """Which bill a 'when is X due' question is about, as a word to look for in bill labels."""
    for s in suppliers:
        if s.lower() in text:
            return s
    for word, focus in (("vat", "VAT"), ("tax", "tax"), ("hmrc", "HMRC"), ("rent", "rent"), ("payroll", "wages"),
                        ("wage", "wages"), ("salar", "wages"), ("staff", "wages"), ("insurance", "insurance"),
                        ("supplier", "supplier"), ("loan", "loan"), ("electric", "utilities"), ("gas", "utilities"),
                        ("energy", "utilities")):
        if word in text:
            return focus
    return ""


def suggest(tokens: list, n: int = 3) -> list:
    """The example questions closest to a message, by shared words. Used when the message is unclear."""
    words = {t for t in tokens if t not in STOPWORDS and len(t) > 2}
    scored = []
    for ex in EXAMPLES:
        ex_words = {t for t in normalise(ex)[0] if t not in STOPWORDS and len(t) > 2}
        overlap = len(words & ex_words)
        scored.append((overlap, -len(ex), ex))
    scored.sort(reverse=True)
    best = [ex for o, _, ex in scored if o > 0][:n]
    for ex in ("Is my business healthy?", "Sales this month", "Will I run out of cash?", "How can I improve cash flow?"):
        if len(best) >= n:
            break
        if ex not in best:
            best.append(ex)
    return best


def _payee_in(lower: str, payees) -> str:
    """A payee the message names, in full or by a distinctive first word ('lothian' for Lothian Mill and Dairy)."""
    for name in sorted(payees, key=len, reverse=True):
        n = name.lower()
        first = n.split()[0]
        if n in lower or (len(first) >= 5 and first not in STOPWORDS and re.search(rf"\b{re.escape(first)}\b", lower)
                          and first not in ("payroll", "card", "cards")):
            return name
    return None


def classify(message: str, today: date, first_day: date = None, suppliers=(), payees=()) -> Parsed:
    keep = {w for name in payees for w in name.lower().split()}
    tokens, fixes = normalise(message, keep)
    text = " ".join(tokens)
    words = set(tokens)
    p = lambda intent, **kw: Parsed(intent, tokens=tokens, corrections=fixes, **kw)

    if not tokens:
        return p("empty")

    lower = message.lower()
    who = next((s for s in suppliers if s.lower() in lower), None) or \
        (_payee_in(lower, suppliers) if suppliers else None)
    delay_words = {"delay", "later", "postpone", "extend", "defer", "push"}
    if ((words & delay_words) and (who or "supplier" in words or "suppliers" in words)) or "pay later" in text:
        m = re.search(r"(\d{1,3})\s*(?:days?|d)\b", lower)
        return p("supplier_delay", supplier=who, days=int(m.group(1)) if m else None)
    if any(rx.search(x) for rx in (TRANSFER_RX, PAY_RX) for x in (lower, text)):
        return p("transfer")
    if re.search(r"\b(apply|application|go ahead|proceed)\b", text):
        return p("apply")
    if re.search(r"\b(who|what) are you\b|\bare you (a |an )?(ai|bot|robot|human|real|person)\b|\byour name\b|"
                 r"\bwho (made|built|created|designed) you\b", text):
        return p("identity")
    if re.search(r"\b(approval|confirmation|one.time|security|otp|verification) (code|pin)s?\b|\b(code|otp)\b.{0,20}"
                 r"\b(show|give|tell|send|read)\b|\b(show|give|tell|send|read)\b.{0,25}\b(code|otp|pin)\b", text):
        return p("code_request")
    if re.search(r"\b(ignore|forget|disregard)\b.{0,30}\b(instructions|rules|prompt|previous)\b|\b(admin|developer|"
                 r"god) mode\b|\bjailbreak\b", text):
        return p("code_request")
    if re.search(r"^(please )?(pay|transfer|wire)\b|^(please )?(send|move)\b.{0,20}(£|\$|¥|\d|money|cash|funds)|\bpay (my|the|our|all)?\s?(rent|bills?|suppliers?|wages|"
                 r"staff|vat|tax|invoices?)\b.{0,15}\b(now|today|for me|immediately)\b|\bmake (a|the) payment\b", text):
        return p("transfer")
    if re.search(r"\b(what does|explain|how (do i|to) read|what is)\b.{0,15}\b(chart|graph|band|shaded|blue line|"
                 r"dashboard|screen)\b", text):
        return p("chart")
    if re.search(r"\b(suspicious|unusual|odd|strange|weird|fraud\w*|scam|anomal\w*|duplicate)\b", text):
        return p("anomalies")
    if re.fullmatch(r"(bye|goodbye|see you|see ya|good night|goodnight|thats all|that is all)( for now)?( thanks)?",
                    text.strip()):
        return p("bye")

    # what-if questions with money in them: "can I afford a £3,000 coffee machine", "what if sales drop 10%"
    wi = _what_if(text, lower)
    if wi:
        return p("what_if", amount=wi.get("amount"), monthly=wi.get("monthly"), sales_pct=wi.get("sales_pct"),
                 label=wi.get("label", ""))
    if re.search(r"\b(good time|right time|ok|okay|wise|sensible|safe)\b.{0,20}\b(buy|spend|invest|purchase|"
                 r"upgrade|replace|expand|refit)\b|\bshould (i|we) (buy|spend|invest|purchase|upgrade|replace|expand)\b",
                 text) and _money(lower) is None:
        return p("spend_check")
    if re.search(r"\b(hire|hiring|employ|recruit|take on (staff|someone|a|an|another|new|more)|new (staff|employee|barista|person|worker)|another "
                 r"(barista|employee|member of staff|person|worker|chef|cook|waiter|assistant))\b", text):
        return p("hire")

    if words <= GREETINGS | THANKS | {"there", "triody", "copilot", "you", "very", "much", "so", "good",
                                      "how", "are", "doing", "lot", "the", "for", "that", "a"}:
        if words & GREETINGS:
            return p("greeting")
        return p("thanks")
    if re.search(r"\b(plan|planning|prepare|budget|get ready)\b.{0,25}\b(next|coming|month|week|weeks|ahead|quarter|"
                 r"christmas|winter)\b", text):
        return p("plan")
    if re.search(r"\b(worried|worry|stressed|scared|anxious|panic\w*|nervous)\b", text):
        return p("health", label="worried")
    if re.search(r"\b(pay|paid|paying)\b.{0,20}\b(vat|tax|hmrc)\b.{0,15}\blate\b|\blate (vat|tax) payment\b|"
                 r"\b(miss|missed|cant pay|can not pay|cannot pay)\b.{0,20}\b(vat|tax|hmrc)\b", text):
        return p("late_tax")
    if re.search(r"\b(help|what can you do|how do you work|what do you do|how does this work|commands|"
                  r"what can i ask|options|menu of questions)\b", text) and not _metric(tokens, text) \
            and not FINANCE_RX.search(text):
        return p("help")

    if re.search(r"\b(weather|joke|jokes|news|football|soccer|cricket|recipe|movie|film|song|music|poem|story|"
                 r"president|prime minister|capital of|translate|homework|game|games|dating|horoscope)\b", text) \
            and not re.search(r"\b(sales|cash|costs?|profit|bills?|vat|tax|loan|money|finance)\b", text):
        return p("off_topic", suggestions=suggest(tokens))

    period = parse_period(tokens, today, first_day)
    metric = _metric(tokens, text)

    personal = re.search(r"\b(my|our|we|i|me|us)\b", text)
    if re.search(r"^(what is|what are|whats|what does|define|definition of|meaning of|explain|what do you mean "
                 r"by|tell me about|how does)\b", text) and not personal:
        for key in sorted(GLOSSARY, key=len, reverse=True):
            if re.search(rf"\b{key}\b", text):
                return p("glossary", term=key)

    if re.search(r"\b(who owes|owed to me|owe me|owes me|unpaid invoices?|debtors|receivables)\b", lower):
        return p("unsupported", unsupported="money customers owe you (unpaid invoices)")
    if re.search(r"\bnext (big |large |major )?payments?\b|\bhow much is (my|the|our) (next |insurance|vat|tax|rent)|"
                 r"\binsurance\b", text) and not re.search(r"\b(paid|spent|per|a year|a month|annual|yearly|monthly)\b",
                                                          text):
        return p("upcoming", label=_bill_focus(text, suppliers))
    if re.search(r"\b(due|owe|upcoming|coming up|outstanding|next (vat|tax|bill|payment|rent|payroll|wages?|salary|"
                 r"salaries|insurance|supplier)|bills? (coming|ahead)|what bills|which bills|pay next|have to pay|need to "
                 r"pay|must pay|payments? coming|enough (cash|money) (for|to pay|to cover))\b", text) \
            and not re.search(r"\b(paid|spent)\b", text):
        return p("upcoming", label=_bill_focus(text, suppliers))
    if re.search(r"\b(vat|tax|hmrc|insurance) (bill|bills|return)\b|\bbills?\b", text) and not period \
            and not re.search(r"\b(paid|spent|electricity|gas|energy|utility|utilities)\b", text):
        return p("upcoming", label=_bill_focus(text, suppliers))
    if re.search(r"\bwhat will (my|the|our) (balance|cash|bank)|\b(balance|cash) (be|look like) (on|at|by|in)\b|"
                 r"\b(balance|cash) (at|on|by) (the )?(end|start)\b", text) and period and period.end > today:
        return p("balance_on", period=period)
    if re.search(r"\bwill (i|we) (be|stay) (ok|okay|alright|fine|safe)\b|\b(make it|get) through\b|\bsurvive\b|"
                 r"\boverdrawn\b|\bin the red\b|\bgo negative\b|\bbelow zero\b|\bcash position\b", text):
        return p("forecast", period=period)
    if re.search(r"\bhow much (money|cash) will (i|we) have\b|\bwhat will (i|we) have in the bank\b", text):
        if period and period.end > today:
            return p("balance_on", period=period)
        return p("forecast")
    if re.search(r"\b(runway|how long (will|does|can) (my |the )?(cash|money)|how long until|how many (days|weeks|months)"
                 r" (until|before|left|of cash))\b", text):
        return p("forecast", period=period)
    if re.search(r"\bmy numbers\b|\bin (simple|plain) (words|english|terms)\b|\bsimply\b", text):
        return p("health")
    if re.search(r"\b(healthy|health|health check|how (is|s) (my|the|our) business|how am i doing|how are we doing|"
                 r"doing (ok|okay|well|good|badly)|in trouble|scorecard|score|kpis?|overall|struggling|"
                 r"(is|am) (my business|i) (ok|okay|alright|safe)|summary|summari[sz]e|overview|big picture|"
                 r"sum up|status)\b", text) and not period and not re.search(r"\bmonthly\b", text):
        return p("health")
    if not period and (re.search(r"\b(best|worst|highest|lowest|strongest|weakest|top|biggest) months?\b|"
                                 r"\b(which|what) month\b|\bmonth by month\b|"
                                 r"\bmonthly (report|sales|costs|profit|figures|breakdown|summary|cash ?flow)\b|\b(each|every|per) "
                                 r"month\b|\btrends?\b|\b(going|trending) (up|down)\b|\bup or down\b|\b(growing|declining|"
                                 r"shrinking)\b|\bmaking a (loss|profit)\b|\bprofitable\b|\bin profit\b|\blosing money\b",
                                 text)):
        return p("months", metric=metric)
    if not period and re.search(r"\b(gross|profit|operating) margin\b|^\W*margins?\W*$|\bmy margins?\b", text) \
            and not re.search(r"\bcosts?\b", text):
        y, mo = _add_months(today.year, today.month, -1)
        return p("report", metric="profit", period=_month_period(y, mo, today))
    if re.search(r"\bwhy\b", text) and (metric or period) and not re.search(r"\bcash\b.{0,15}\b(tight|short|low)\b", text):
        if not period:
            y, mo = _add_months(today.year, today.month, -1)
            period = _month_period(y, mo, today)
        return p("why_change", metric=metric or "profit", period=period)
    if re.search(r"\b(improve|increase|boost|grow|reduce|cut|lower|save|saving|better|fix|optimi[sz]e)\b.{0,40}\b"
                 r"(cash ?flow|cash|sales|costs?|profits?|margins?|money|business|spending|expenses|bills|wages)\b", text) \
            or re.search(r"\b(advice|tips?|suggest\w*|recommend\w*|what should (i|we) do|what can (i|we) do|"
                         r"next steps?|action plan|how (do|can) (i|we) (make more|earn more|save))\b", text):
        if not FINANCE_RX.search(text):
            focus = ("cost" if re.search(r"\b(cost|costs|spending|expenses|bills|wages|save|saving|cut|reduce|lower)\b",
                                         text) else
                     "sales" if re.search(r"\b(sales|revenue|takings|customers|make more|earn more)\b", text) else "")
            return p("advice", label=focus)

    pair = _split_compare(text)
    if pair:
        a = parse_period(pair[0].split(), today, first_day)
        b = parse_period(pair[1].split(), today, first_day)
        if a and re.search(r"\bsame day\b", pair[1]) and a.compare_start:
            b = Period(a.compare_start, a.compare_end, a.compare_label, "day")
            return p("compare", metric=metric or "summary", period=a, compare=b)
        if a and b:
            return p("compare", metric=metric or "summary", period=a, compare=b)
        if b and not a and b.start <= today:
            same = {"month": "this month", "week": "this week", "year": "this year", "quarter": "this quarter"}
            if b.kind in same:
                a = parse_period(same[b.kind].split(), today, first_day)
                return p("compare", metric=metric or "summary", period=a, compare=b)
        if a and re.search(r"\bcompare\b", text):
            return p("report", metric=metric or "summary", period=a)

    payee = _payee_in(lower, payees) if payees else None
    if payee and not re.search(r"\b(delay|later|postpone|defer)\b", text):
        return p("report", metric="payee", period=period, supplier=payee)
    if re.search(r"\b(best|worst|busiest|quietest|slowest|strongest|weakest|biggest) weeks?\b|\bweek by week\b|"
                 r"\bweekly (sales|takings|figures)\b|\b(average|avg|typical) week\b", text) and not re.search(r"\baverage weekly\b", text):
        return p("weeks", period=period)
    if re.search(r"\b(days?|how often) (was|were|am|are) (i|we) open\b|\btrading days\b|\bopen days\b|\bdays open\b", text):
        return p("report", metric="trading_days", period=period)

    wd = next((WEEKDAYS[t] for t in tokens if t in WEEKDAYS), None)
    day_of_week = re.search(r"\b(days? of the week|weekdays?|weekends?|by day|each day|which day of)\b", text)
    which_day = re.search(r"\b(which|what) day\b|\b(busiest|quietest|best|worst|slowest) days?\b", text)
    if not (period and period.kind == "day") and (wd is not None or day_of_week or (which_day and not period)):
        return p("weekdays", period=period, weekday=wd)

    if re.search(r"\b(which|what|who)\b.{0,30}\b(supplier|suppliers|payee|cost|costs|expense|expenses|bill|spend)\b.{0,30}"
                 r"\b(most|biggest|largest|highest|top)\b", text) \
            or re.search(r"\b(biggest|largest|top|main|highest)\b.{0,15}\b(suppliers?|payees?|costs?|expenses?|bills?|"
                         r"outgoings|spend)\b", text) \
            or re.search(r"\bwhere (does|did|is) (my|the|our) money (go|going|went)\b|\bwho do (i|we) pay\b", text):
        return p("top", period=period)

    if re.search(r"\bsmes?\b|\bsmall (and|or) medium|\bsmall business(es)?\b|\bmicro business", text) \
            and not metric and not period and not FINANCE_RX.search(text):
        return p("sme_info")
    if re.search(r"\b(upload|import|connect|add|load|link|sync)\b.{0,30}\b(data|bank|csv|file|account|"
                  r"transactions|ledger|statement|books)\b", text) \
            or re.search(r"\b(more|extra|additional|other|new|own|real) (data|transactions|accounts?|shops?)\b", text) \
            or re.search(r"\bwhat (data|information|info)\b|\bwhere (do|does) (the|your|my) (data|numbers|figures)\b"
                         r"|\bhow far back\b|\bdata (coverage|range|source)\b|\bwhich data\b", text):
        return p("data_info")

    sales_words = METRIC_WORDS["sales"] | {"best", "top", "popular", "selling", "bestselling"}
    if re.search(r"\bstock (levels?|count|on hand|left|holding)\b|\bhow much stock\b", text):
        return p("unsupported", unsupported=UNSUPPORTED["stock"][0])
    for key, (label, group) in UNSUPPORTED.items():
        if not words & group:
            continue
        if key == "products" and not words & sales_words:
            continue
        return p("unsupported", unsupported=label, period=period, metric=metric)
    if re.search(r"\b(how many|number of)\b", text) and "transactions" not in text and "payments" not in text:
        if words & {"staff", "people", "workers", "employees"}:
            return p("unsupported", unsupported=UNSUPPORTED["staff"][0], period=period)
        if metric == "sales":
            return p("unsupported", unsupported="the number of sales or customers", period=period)

    if FINANCE_RX.search(text) and not (metric == "loan" and period):
        return p("financing")
    if re.search(r"\b(transactions?|memo|recent|ledger|statement)\b", text) and metric in (None, "transactions") \
            and not (metric == "transactions" and period):
        return p("recent")
    if not period and metric in ("profit", "costs") and words & {"margin", "margins", "trend", "trends", "breakeven"}:
        return p("analysis")

    future = period is not None and period.start > today
    if metric and future:
        return p("future", metric=metric, period=period)
    if metric:
        return p("report", metric=metric, period=period)
    if period and re.search(r"\b(how am i doing|how are we doing|how did (i|we) do|how is (the|my) business|"
                            r"performance|summary|overview|report)\b", text):
        return p("report", metric="summary", period=period)
    if re.search(r"\b(break ?even|analys\w*|analyz\w*|trends?|performance|kpis?|health|how am i doing|"
                  r"how is (the|my) business|how are we doing|month on month|insights?|advice|tips|improve)\b", text):
        return p("analysis")
    if re.search(r"\b(why|explain|reason|cause|tight|short|shortfall)\b", text):
        return p("explain")
    if future or re.search(r"\b(run out|forecast|enough|afford|alert|predict\w*|projection|future|survive|"
                           r"cash position|weeks? ahead|coming weeks)\b", text):
        return p("forecast", period=period)
    if re.search(r"\b(balance|bank balance|in the bank|cash on hand|how much (cash|money) do i have)\b", text):
        return p("balance")
    if re.search(r"\bcash\b", text):
        return p("forecast", period=period)
    if period:
        return p("report", metric="summary", period=period)
    return p("unclear", suggestions=suggest(tokens))
