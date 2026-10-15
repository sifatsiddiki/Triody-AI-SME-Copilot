"""30 hand-written business cases with the answer a careful adviser would give.

The labels were written before the matcher was run on them. They come from reasoning about the
case ("needs money in 3 days, so a 5-day loan is too slow"), not from the scoring formula.
`best` is the single best product, `ok` lists answers that are also reasonable, and None means
"no product should be recommended". The set is small and written by the team, so treat the
accuracy as a sanity check, not as proof of real-world performance.
"""
CORRECTIONS = []   # every label change made after seeing results is logged here with its reason


def _c(cid, months, rev, fcf, amount, term, urgency, purpose, credit, card=0.0, recv=0.0,
       coll=False, sup=False, best=None, ok=None, why=""):
    return {"id": cid, "months_trading": months, "annual_revenue": rev, "monthly_free_cash_flow": fcf,
            "amount": amount, "term_months": term, "urgency_days": urgency, "purpose": purpose,
            "credit_band": credit, "card_share": card, "receivables": recv, "collateral": coll,
            "supplier_relationship": sup, "best": best, "ok": ok if ok is not None else [best],
            "why": why}


CASES = [
    # A. Cash gaps for card-based shops
    _c("A1", 30, 240000, 3000, 8000, 4, 3, "cash_gap", "good", card=.85, sup=True, best="overdraft",
       why="Money is needed in 3 days. Only the overdraft and the costly cash advance arrive that fast."),
    _c("A2", 30, 240000, 3000, 8000, 4, 14, "cash_gap", "good", card=.85, sup=True, best="wc_loan",
       ok=["wc_loan", "overdraft"], why="Two weeks is enough for the cheaper 5-day loan."),
    _c("A3", 8, 70000, 900, 3500, 3, 4, "cash_gap", "fair", card=.8, sup=True, best="supplier_credit",
       ok=["supplier_credit", "overdraft"], why="Small, short gap and a supplier relationship: a supplier extension is cheapest."),
    _c("A4", 5, 45000, 700, 4000, 3, 3, "cash_gap", "weak", card=.9, best=None,
       why="Five months of trading and a weak credit profile. Nothing in the catalogue is offered."),
    _c("A5", 14, 130000, 1800, 12000, 6, 10, "cash_gap", "fair", card=.7, best="wc_loan",
       why="Mid-size gap, 10 days to arrange: the working capital loan is cheaper than the cash advance."),
    # B. Businesses waiting for customers to pay
    _c("B1", 40, 600000, 5000, 40000, 3, 7, "cash_gap", "fair", card=.05, recv=90000, best="invoice_fin",
       why="Customers owe 90k. Invoice finance is repaid by those invoices, a loan would add 13k a month."),
    _c("B2", 10, 150000, 1200, 15000, 2, 5, "cash_gap", "weak", recv=30000, best="invoice_fin",
       why="Weak credit and a young business, but unpaid invoices support invoice finance."),
    _c("B3", 60, 1200000, 12000, 120000, 12, 21, "cash_gap", "good", recv=300000, best="credit_line",
       ok=["credit_line", "invoice_fin"], why="Strong business: the credit line is cheaper, invoice finance also works."),
    _c("B4", 36, 400000, 2500, 60000, 2, 4, "cash_gap", "fair", recv=20000, best=None,
       why="60k in 2 months with only 20k of invoices. No product fits as asked."),
    _c("B5", 18, 260000, 3200, 25000, 3, 6, "cash_gap", "fair", card=.1, recv=35000, best="invoice_fin",
       why="The overdraft repayment would be far above free cash flow. Invoice finance costs 300 a month."),
    # C. Equipment purchases
    _c("C1", 20, 180000, 2800, 22000, 36, 30, "equipment", "fair", best="equipment_fin",
       ok=["equipment_fin", "young_loan"], why="Equipment finance has the lowest rate and is secured on the equipment."),
    _c("C2", 60, 900000, 9000, 150000, 60, 45, "equipment", "good", coll=True, best="equipment_fin",
       ok=["equipment_fin", "secured_loan"], why="Equipment finance does not put other assets at risk."),
    _c("C3", 8, 55000, 600, 6000, 24, 20, "equipment", "fair", best="young_loan",
       why="Too young for equipment finance. The young-business loan fits."),
    _c("C4", 30, 300000, 3500, 45000, 48, 30, "equipment", "good", best="equipment_fin",
       ok=["equipment_fin", "term_loan"], why="Lowest rate for equipment."),
    _c("C5", 15, 90000, 300, 30000, 36, 30, "equipment", "fair", best=None,
       why="Eligible, but about 940 a month against 300 of free cash flow. Should not be recommended."),
    # D. Growth
    _c("D1", 48, 800000, 7000, 80000, 48, 30, "growth", "good", best="term_loan",
       why="Established, good credit, no rush: the term loan is cheapest."),
    _c("D2", 30, 220000, 2500, 40000, 24, 30, "growth", "good", best="term_loan",
       ok=["term_loan", "young_loan"], why="Lowest rate, 30 days is enough."),
    _c("D3", 24, 160000, 2000, 20000, 12, 20, "growth", "fair", best="young_loan",
       why="Cheaper than the working capital loan and arrives within 20 days."),
    _c("D4", 12, 100000, 1500, 100000, 60, 30, "growth", "good", best=None,
       why="100k after one year of trading with no security. Nothing fits."),
    _c("D5", 100, 2500000, 30000, 400000, 84, 45, "growth", "good", coll=True, best="secured_loan",
       ok=["secured_loan", "term_loan"], why="Large and long: the secured loan saves a lot of interest."),
    # E. Stock
    _c("E1", 36, 500000, 4000, 30000, 4, 10, "stock", "fair", card=.3, sup=True, best="wc_loan",
       why="Four months is above the supplier extension limit."),
    _c("E2", 36, 500000, 4000, 30000, 2, 5, "stock", "fair", card=.3, sup=True, best="supplier_credit",
       ok=["supplier_credit", "wc_loan"], why="Two months and a supplier relationship: the extension is cheapest."),
    _c("E3", 20, 150000, 1800, 12000, 6, 14, "stock", "fair", card=.2, best="wc_loan",
       why="No supplier relationship, mid-size need."),
    _c("E4", 10, 80000, 500, 20000, 12, 14, "stock", "weak", best=None,
       why="Young, weak credit, 20k. Nothing fits."),
    _c("E5", 26, 400000, 3000, 60000, 24, 20, "stock", "good", sup=True, best="credit_line",
       why="Cheaper and lighter on cash flow than the working capital loan."),
    # F. Edge cases
    _c("F1", 3, 40000, 400, 8000, 24, 30, "growth", "fair", best="young_loan",
       why="Three months of trading: only the young-business loan accepts that."),
    _c("F2", 7, 60000, 200, 3000, 3, 2, "cash_gap", "weak", card=.9, best=None,
       why="Only the 20 percent cash advance is eligible, 1,200 a month against 200 of free cash flow."),
    _c("F3", 24, 350000, 2000, 15000, 5, 3, "cash_gap", "good", card=.6, sup=True, best="overdraft",
       why="Needed in 3 days and the overdraft is the cheapest option that arrives in time."),
    _c("F4", 18, 210000, 2200, 18000, 6, 10, "cash_gap", "fair", card=.1, recv=25000, best="invoice_fin",
       why="Invoice finance costs about 216 a month. The loans would cost 1,500 or more."),
    _c("F5", 60, 700000, 6000, 25000, 12, 14, "cash_gap", "good", card=.4, sup=True, best="credit_line",
       ok=["credit_line", "wc_loan"], why="Cheapest and flexible, arrives within 14 days."),
]


# ----------------------------------------------------------------------------- held-out set
# 14 cases for the live catalogue of sourced UK products (catalog.json). They were written on
# 14 October 2026, after the stock rule was added and before the matcher was ever run on them.
# They test the new parts: the HMRC payment plan, existing-customer rules, legal form, and stock
# purchases with tight cash flow. Same author as above, so the same circularity warning applies.

def _h(cid, months, rev, fcf, amount, term, urgency, purpose, credit, legal="ltd", providers=(),
       tax=0.0, card=0.0, recv=0.0, coll=False, sup=False, best=None, ok=None, why=""):
    c = _c(cid, months, rev, fcf, amount, term, urgency, purpose, credit, card=card, recv=recv,
           coll=coll, sup=sup, best=best, ok=ok, why=why)
    c.update({"legal_form": legal, "existing_providers": list(providers), "tax_due": tax})
    return c


HELDOUT_CASES = [
    _h("H1", 30, 200000, 1500, 6000, 6, 20, "cash_gap", "fair", providers=["Lloyds Bank"], tax=9000,
       card=.8, best="hmrc_ttp", ok=["hmrc_ttp", "lloyds_overdraft"],
       why="The gap comes from a VAT bill. A payment plan with HMRC costs 7.75% and needs no new lender."),
    _h("H2", 30, 200000, 1500, 6000, 6, 20, "cash_gap", "fair", providers=["Lloyds Bank"], card=.8,
       best="lloyds_overdraft", why="No tax bill. The existing bank's overdraft is the cheapest fast option."),
    _h("H3", 24, 150000, 2000, 6000, 6, 2, "cash_gap", "fair", card=.3, best="iwoca_flexi",
       why="Not a Lloyds or Cardnet customer and money is needed in 2 days. Only iwoca fits. Expensive but affordable."),
    _h("H4", 30, 180000, 1200, 12000, 4, 14, "stock", "fair", providers=["Lloyds Bank"], card=.7,
       best="lloyds_overdraft",
       why="Christmas stock. Sales of the stock repay most of it, so the overdraft is affordable and cheapest."),
    _h("H5", 40, 160000, 600, 20000, 24, 30, "stock", "good", best=None,
       why="Two years is too long for stock to repay itself. About 900 a month against 600 of free cash flow."),
    _h("H6", 36, 300000, 2500, 30000, 48, 30, "equipment", "good", best="asset_finance",
       ok=["asset_finance", "funding_circle"],
       why="Hire purchase is secured on the machine itself. Funding Circle's rate is only a best case."),
    _h("H7", 30, 400000, 2000, 30000, 3, 14, "cash_gap", "fair", card=.05, recv=60000, best="invoice_finance",
       why="Customers owe 60k. Invoice finance costs about 640 a month. A loan would need 10k a month."),
    _h("H8", 8, 50000, 700, 8000, 36, 45, "growth", "fair", legal="sole_trader", best="start_up_loan",
       why="Eight months old, 45 days to wait. The Start Up Loan is cheap and comes with mentoring."),
    _h("H9", 3, 30000, 500, 5000, 3, 5, "cash_gap", "weak", legal="sole_trader", card=.9, best=None,
       why="Three months old with a weak credit profile and no tax bill. Nothing should be offered."),
    _h("H10", 30, 200000, 1500, 18000, 10, 20, "cash_gap", "fair", providers=["Lloyds Bank"], tax=20000,
       card=.6, best=None,
       why="18k back in 10 months is about 1,900 a month against 1,500 of free cash flow. A longer HMRC plan by phone is the real answer, outside the model."),
    _h("H11", 14, 120000, 1500, 4000, 4, 3, "cash_gap", "weak", legal="sole_trader",
       providers=["Lloyds Cardnet"], card=.9, best="cardnet_mca", ok=["cardnet_mca", None],
       why="Weak credit and money needed in 3 days. The card advance is the only option and is affordable, though costly."),
    _h("H12", 20, 160000, 2000, 15000, 2, 7, "stock", "fair", sup=True, best="supplier_terms",
       why="Two months of stock from a supplier already used. Longer terms are far cheaper than iwoca."),
    _h("H13", 14, 90000, 900, 15000, 36, 30, "equipment", "fair", legal="sole_trader", best="start_up_loan",
       ok=["start_up_loan", "asset_finance"],
       why="7.5% against about 17% for hire purchase on a fair profile. 30 days is enough for the Start Up Loan."),
    _h("H14", 50, 600000, 3000, 60000, 36, 45, "growth", "good", providers=["Lloyds Bank"], coll=True,
       best="funding_circle", ok=["funding_circle", "secured_loan"],
       why="Unsecured growth loan keeps the property out of it. The secured loan is a fair second."),
]


# ----------------------------------------------------------------------------- second held-out set
# 15 more cases for the live UK catalogue, written on 14 October 2026 after the external review that
# flagged the 30 original cases as no longer a fair test of the stock rule. Labels were written from
# the business situation alone and frozen before the matcher was run on any of them. The results in
# docs/EVALUATION.md are from the first run. Same project wrote cases and catalogue, so the
# circularity warning still applies: the fair test is cases from someone outside the team.

NEW_CASES = [
    _h("N1", 30, 190000, 1200, 4000, 6, 10, "cash_gap", "fair", providers=["Lloyds Bank"], tax=4000, card=.8,
       best="hmrc_ttp", ok=["hmrc_ttp", "lloyds_overdraft"],
       why="The whole gap is a VAT bill. Paying HMRC in instalments costs least and adds no new lender."),
    _h("N2", 30, 260000, 2500, 12000, 9, 10, "cash_gap", "fair", providers=["Lloyds Bank"], tax=4000, card=.8,
       best="lloyds_overdraft", ok=["lloyds_overdraft", "iwoca_flexi"],
       why="The gap is three times the tax bill, so a tax plan cannot cover it. The existing bank's overdraft can."),
    _h("N3", 24, 90000, 900, 9000, 36, 30, "equipment", "fair", legal="sole_trader",
       best="start_up_loan", ok=["start_up_loan", "asset_finance"],
       why="Two years old, a month to wait. The government-backed loan is far cheaper than hire purchase on a fair profile."),
    _h("N4", 84, 520000, 4000, 40000, 60, 21, "equipment", "good",
       best="asset_finance", ok=["asset_finance", "funding_circle"],
       why="A machine over five years: hire purchase is secured on the machine at a real rate. Funding Circle's rate is a best case."),
    _h("N5", 24, 110000, 1500, 3000, 6, 2, "cash_gap", "weak", providers=["Lloyds Cardnet"], card=.85,
       best="cardnet_mca", ok=["cardnet_mca", None],
       why="Weak credit, cash needed in 2 days, most sales on card through Cardnet. The card advance is the only route."),
    _h("N6", 18, 380000, 2000, 20000, 3, 14, "cash_gap", "fair", card=.05, recv=40000,
       best="invoice_finance",
       why="Customers owe 40k. Borrowing against those invoices costs little each month. A loan would need about 7k a month."),
    _h("N7", 2, 25000, 400, 5000, 24, 40, "growth", "fair", legal="sole_trader",
       best="start_up_loan",
       why="Two months old. Only the Start Up Loan lends this early, and the repayment is affordable."),
    _h("N8", 40, 400000, 3000, 50000, 6, 20, "cash_gap", "fair", providers=["Lloyds Bank"], card=.6,
       best=None,
       why="50k back in 6 months is over 8k a month against 3k of free cash flow. Nothing should be recommended."),
    _h("N9", 20, 140000, 1000, 8000, 3, 10, "stock", "fair", card=.7, sup=True,
       best="supplier_terms", ok=["supplier_terms", "iwoca_flexi"],
       why="Three months of stock from a supplier already used. Longer terms cost a fraction of an unsecured loan."),
    _h("N10", 36, 900000, 5000, 200000, 120, 60, "growth", "good", coll=True,
       best="secured_loan",
       why="Ten years is beyond Funding Circle's six. A loan secured on property is the cheapest long money."),
    _h("N11", 48, 420000, 3500, 30000, 12, 10, "cash_gap", "fair", tax=30000, card=.5,
       best="hmrc_ttp",
       why="A 30k tax bill over 12 months is about 2,600 a month against 3,500 of free cash flow. Affordable and cheapest."),
    _h("N12", 12, 60000, 600, 2000, 3, 7, "cash_gap", "weak", legal="sole_trader",
       best=None,
       why="Weak credit, no card sales, no unpaid invoices and no tax bill. No product should be offered."),
    _h("N13", 30, 300000, 2500, 20000, 4, 7, "stock", "good", providers=["Lloyds Bank"], card=.7,
       best="lloyds_overdraft", ok=["lloyds_overdraft", "iwoca_flexi"],
       why="Seasonal stock for four months with the existing bank. The overdraft is cheaper than iwoca and the stock repays it."),
    _h("N14", 61, 150000, 1000, 12000, 48, 20, "equipment", "fair", legal="sole_trader",
       best="asset_finance",
       why="Just over five years old, so too old for a Start Up Loan, and a sole trader, so not Funding Circle. Hire purchase fits."),
    _h("N15", 36, 240000, 3000, 7000, 3, 1, "cash_gap", "fair", card=.2,
       best="iwoca_flexi", ok=["iwoca_flexi", None],
       why="Cash needed tomorrow, no existing bank product, no tax bill. iwoca is costly but the only fast option, and affordable."),
]
