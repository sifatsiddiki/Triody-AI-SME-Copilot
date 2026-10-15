"""Regression tests for reading the owner's message: intents, typos and periods.

Each case here is a question that once fell through to the generic help text, or picked the wrong
period. The books of the demo café run from 18 April to 14 October 2026.
"""
import unittest
from datetime import date

from tests.helpers import ROOT  # noqa: F401
from triody.intent import classify, correct, edit_distance, normalise, parse_period

TODAY = date(2026, 10, 14)
FIRST = date(2026, 4, 18)
SUPPLIERS = ["Leith Coffee Roasters", "Lothian Mill and Dairy"]


def parse(text):
    return classify(text, TODAY, FIRST, SUPPLIERS)


def period(text):
    tokens, _ = normalise(text)
    return parse_period(tokens, TODAY, FIRST)


class IntentRoutingTests(unittest.TestCase):
    CASES = {
        "Today's sales": ("report", "sales"),
        "sales": ("report", "sales"),
        "this year sale": ("report", "sales"),
        "this month sales": ("report", "sales"),
        "October sales": ("report", "sales"),
        "how much did I sell in september": ("report", "sales"),
        "what's my turnover this week": ("report", "sales"),
        "what were my expenses last month": ("report", "costs"),
        "wages this month": ("report", "payroll"),
        "staff costs last month": ("report", "payroll"),
        "rent in august": ("report", "rent"),
        "electricity bill last month": ("report", "utilities"),
        "how much VAT did I pay this year": ("report", "tax"),
        "card fees this year": ("report", "fees"),
        "loan repayments this year": ("report", "loan"),
        "profit last month": ("report", "profit"),
        "cash flow this month": ("report", "net_cash"),
        "how many transactions last week": ("report", "transactions"),
        "average daily sales last month": ("report", "average"),
        "best day this month": ("report", "best_day"),
        "slowest day last week": ("report", "worst_day"),
        "how are we doing this month": ("report", "summary"),
        "november sales": ("future", "sales"),
        "sales next month": ("future", "sales"),
        "Will I run out of cash?": ("forecast", None),
        "Why is cash tight?": ("explain", None),
        "How are my margins and costs?": ("analysis", None),
        "break even": ("analysis", None),
        "top suppliers": ("top", None),
        "What financing fits?": ("financing", None),
        "Can I get time to pay with HMRC?": ("financing", None),
        "may I get a loan": ("financing", None),
        "apply": ("apply", None),
        "show recent transactions": ("recent", None),
        "what's my balance": ("balance", None),
        "what is an SME": ("sme_info", None),
        "are small businesses SMEs": ("sme_info", None),
        "can I add more data": ("data_info", None),
        "what data do you have": ("data_info", None),
        "how far back does your data go": ("data_info", None),
        "how many customers did I have": ("unsupported", None),
        "which coffee sells best": ("unsupported", None),
        "how many staff do I have": ("unsupported", None),
        "transfer £9,000 to account 12345678": ("transfer", None),
        "delay Lothian Mill and Dairy payment by 5 days": ("supplier_delay", None),
        "hi": ("greeting", None),
        "thanks": ("thanks", None),
        "help": ("help", None),
        "asdfgh": ("unclear", None),
        "what is the weather": ("off_topic", None),
    }

    def test_every_case(self):
        for text, (intent, metric) in self.CASES.items():
            with self.subTest(text=text):
                p = parse(text)
                self.assertEqual(p.intent, intent)
                if metric:
                    self.assertEqual(p.metric, metric)

    def test_supplier_delay_reads_the_days(self):
        p = parse("delay Lothian Mill and Dairy payment by 5 days")
        self.assertEqual((p.supplier, p.days), ("Lothian Mill and Dairy", 5))


class TypoTests(unittest.TestCase):
    def test_common_typos_are_fixed(self):
        for typo, word in [("slaes", "sales"), ("revnue", "revenue"), ("expences", "expenses"),
                           ("octber", "october"), ("septmber", "september"), ("forcast", "forecast"),
                           ("finacing", "financing"), ("salse", "sales")]:
            self.assertEqual(correct(typo), word, typo)

    def test_ordinary_words_are_left_alone(self):
        for w in ("save", "send", "rest", "weather", "change", "what", "much", "shop", "list", "case"):
            self.assertEqual(correct(w), w)

    def test_typo_questions_route_like_the_clean_ones(self):
        self.assertEqual(parse("slaes today").metric, "sales")
        self.assertEqual(parse("wat is my revnue").metric, "sales")
        self.assertEqual(parse("forcast").intent, "forecast")
        self.assertEqual(parse("finacing options").intent, "financing")
        self.assertEqual(parse("octber sales").period.start, date(2026, 10, 1))

    def test_swap_of_two_letters_counts_once(self):
        self.assertEqual(edit_distance("slaes", "sales"), 1)


class PeriodTests(unittest.TestCase):
    def check(self, text, start, end, to_date=None):
        p = period(text)
        self.assertIsNotNone(p, text)
        self.assertEqual((p.start, p.end), (start, end), text)
        if to_date is not None:
            self.assertEqual(p.to_date, to_date, text)
        return p

    def test_today_is_the_last_day_in_the_books(self):
        p = self.check("sales today", TODAY, TODAY)
        self.assertEqual(p.compare_start, date(2026, 10, 7))   # same weekday last week

    def test_yesterday(self):
        self.check("sales yesterday", date(2026, 10, 13), date(2026, 10, 13))

    def test_this_week_and_last_week(self):
        self.check("this week", date(2026, 10, 12), TODAY, True)
        self.check("last week", date(2026, 10, 5), date(2026, 10, 11))

    def test_this_month_is_october_to_date(self):
        p = self.check("this month", date(2026, 10, 1), TODAY, True)
        self.assertEqual((p.compare_start, p.compare_end), (date(2026, 9, 1), date(2026, 9, 14)))

    def test_october_is_october_not_september(self):
        self.check("october sales", date(2026, 10, 1), TODAY, True)
        self.check("sales in Oct 2026", date(2026, 10, 1), TODAY)

    def test_last_month_is_september(self):
        p = self.check("last month", date(2026, 9, 1), date(2026, 9, 30), False)
        self.assertEqual((p.compare_start, p.compare_end), (date(2026, 8, 1), date(2026, 8, 31)))

    def test_this_year_is_january_to_date(self):
        self.check("this year", date(2026, 1, 1), TODAY, True)
        self.check("year to date", date(2026, 1, 1), TODAY)
        self.check("ytd", date(2026, 1, 1), TODAY)

    def test_last_year(self):
        self.check("last year", date(2025, 1, 1), date(2025, 12, 31))

    def test_rolling_windows(self):
        self.check("last 30 days", date(2026, 9, 15), TODAY)
        self.check("past two weeks", date(2026, 10, 1), TODAY)
        self.check("last fortnight", date(2026, 10, 1), TODAY)

    def test_quarters(self):
        self.check("revenue in Q3", date(2026, 7, 1), date(2026, 9, 30))
        self.check("this quarter", date(2026, 10, 1), TODAY)
        self.check("last quarter", date(2026, 7, 1), date(2026, 9, 30))

    def test_explicit_dates_and_ranges(self):
        self.check("sales on 2026-10-10", date(2026, 10, 10), date(2026, 10, 10))
        self.check("sales on 10/10/2026", date(2026, 10, 10), date(2026, 10, 10))
        self.check("sales from 1 sept to 30 sept", date(2026, 9, 1), date(2026, 9, 30))
        self.check("between 1 August and 15 August", date(2026, 8, 1), date(2026, 8, 15))
        self.check("since 1 october", date(2026, 10, 1), TODAY)

    def test_named_months(self):
        self.check("sales in may", date(2026, 5, 1), date(2026, 5, 31))
        self.check("august", date(2026, 8, 1), date(2026, 8, 31))
        self.check("since august", date(2026, 8, 1), TODAY)

    def test_may_the_verb_is_not_a_month(self):
        self.assertIsNone(period("may I see the forecast please"))

    def test_future_periods_are_marked(self):
        self.assertGreater(period("next month").start, TODAY)
        self.assertGreater(period("next week").start, TODAY)

    def test_no_period(self):
        self.assertIsNone(period("sales"))


if __name__ == "__main__":
    unittest.main()
