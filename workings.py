"""Optional nav_workings.xlsx: the numbers laid out with live Excel formulas, so a reviewer can
re-add the bridge in Excel and see it tie to the statement of net assets."""
from openpyxl import Workbook
from openpyxl.styles import Font

BOLD = Font(bold=True)
NUM = "#,##0.00;(#,##0.00)"


def write_workings(path, transactions, review, nav):
    wb = Workbook()

    ws = wb.active
    ws.title = "Transactions"
    cols = ["date", "description", "reference", "debit", "credit", "balance", "category", "lp_code", "company"]
    ws.append(cols)
    for t in transactions:
        ws.append([t.get(c) for c in cols])
    n = len(transactions) + 1
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=6):
        for c in row:
            c.number_format = NUM
    ws.column_dimensions["B"].width = 70

    s = wb.create_sheet("By category")
    s.append(["category", "debits", "credits"])
    cats = sorted({t["category"] for t in transactions})
    for i, cat in enumerate(cats, start=2):
        s.append([cat, f'=SUMIF(Transactions!G2:G{n},A{i},Transactions!D2:D{n})',
                  f'=SUMIF(Transactions!G2:G{n},A{i},Transactions!E2:E{n})'])

    b = nav["bridge"]
    br = wb.create_sheet("NAV bridge")
    rows = [
        ("NAV at 30 Sep 2022", b["nav_q3"]),
        ("+ Capital called", b["capital_called"]),
        ("- Distributions", -b["distributions"]),
        ("+ Income", b["income"]),
        ("- Expenses", -b["expenses"]),
        ("+ Realized gain", b["realized_gain"]),
        ("+ Change in unrealized gain", b["change_in_unrealized"]),
    ]
    for r in rows:
        br.append(r)
    br.append(("NAV at 31 Dec 2022 (bridge)", "=SUM(B1:B7)"))
    br.append(())
    br.append(("Closing cash", nav["closing_cash"]))
    br.append(("Investments at fair value", nav["investments_usd"]))
    br.append(("Liabilities", -nav["liabilities"]))
    br.append(("NAV at 31 Dec 2022 (net assets)", "=SUM(B10:B12)"))
    br.append(("Difference", "=B13-B8"))
    for cell in ("A8", "A13", "A14"):
        br[cell].font = BOLD
    for row in br.iter_rows(min_col=2, max_col=2):
        for c in row:
            c.number_format = NUM
    br.column_dimensions["A"].width = 34
    br.column_dimensions["B"].width = 20

    rv = wb.create_sheet("Review")
    rcols = ["company", "currency", "q3_fair_value", "q4_fair_value", "change_pct", "q3_method", "q4_method",
             "q4_value_usd_in_nav", "flags", "reason"]
    rv.append(rcols)
    for e in review["companies"]:
        rv.append([", ".join(e[c]) if c == "flags" else e.get(c) for c in rcols])
    rv.append(())
    rv.append(("Threshold %", review["threshold_pct"]))
    rv.append(("EUR/USD", review["eurusd_rate"]))
    wb.save(path)
