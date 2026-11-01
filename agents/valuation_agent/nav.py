"""Q4 NAV and the NAV bridge. Everything in Decimal; the bridge must land on NAV to the cent."""
from __future__ import annotations

from decimal import Decimal

from agents.bank_agent.rules import normalize_name
from agents.common.money import D, to_json_number

ZERO = Decimal("0")


def _sum(txns, category, side):
    return sum((D(t[side]) for t in txns if t["category"] == category), ZERO)


def compute_nav(txns: list[dict], opening_cash, q3, valuation: dict) -> tuple[dict, list[str]]:
    """Return (nav dict ready for nav.json, list of check failures).

    q3: Q3Position. valuation: output of review_marks for stakes held at 31 Dec.
    """
    checks: list[str] = []
    opening_cash = D(opening_cash)
    if opening_cash != q3.cash:
        checks.append(f"Bank opening balance {opening_cash} != Q3 cash {q3.cash}")

    # ---- statement of net assets at 31 Dec
    movements = sum((D(t["credit"]) - D(t["debit"]) for t in txns), ZERO)
    closing_cash = opening_cash + movements
    if txns and D(txns[-1]["balance"]) != closing_cash:
        checks.append("Closing cash does not equal the last running balance")
    investments = sum((v["value_usd"] for v in valuation.values()), ZERO)
    liabilities = sum(q3.q4_accruals.values(), ZERO)
    nav_q4 = closing_cash + investments - liabilities

    # ---- bridge components
    capital_called = _sum(txns, "capital_call", "credit")
    distributions = _sum(txns, "distribution", "debit")
    dividends = _sum(txns, "dividend_income", "credit")
    interest = _sum(txns, "interest_income", "credit")
    income = dividends + interest
    management_fee = _sum(txns, "management_fee", "debit")
    other_expenses = _sum(txns, "expense", "debit")
    bank_charges = _sum(txns, "bank_charges", "debit")
    refunds = _sum(txns, "expense_refund", "credit")
    accrued_q4 = liabilities
    expenses = management_fee + other_expenses + bank_charges - refunds + accrued_q4
    settlements = _sum(txns, "accrual_settlement", "debit")

    # accrual settlements must clear liabilities that existed at 30 Sep (otherwise they're expenses)
    open_liabs = list(q3.liabilities.values())
    for t in txns:
        if t["category"] == "accrual_settlement":
            amt = D(t["debit"])
            if amt in open_liabs:
                open_liabs.remove(amt)
            else:
                checks.append(f"accrual_settlement {t['date']} {amt} matches no 30 Sep liability")
    if open_liabs:
        checks.append(f"30 Sep liabilities not settled in Q4 and not in the 31 Dec accruals: {open_liabs}")

    # realized gain: bank proceeds minus Q3 cost of the stake sold
    realized = ZERO
    q3_by = {normalize_name(k): v for k, v in q3.holdings.items()}
    sales_detail = []
    for t in txns:
        if t["category"] == "sale_proceeds":
            h = q3_by.get(normalize_name(t["company"]))
            if h is None:
                checks.append(f"Sale of {t['company']} but it was not held at 30 Sep")
                continue
            gain = D(t["credit"]) - h.cost_usd
            realized += gain
            sales_detail.append({"company": h.company, "proceeds": to_json_number(t["credit"]),
                                 "cost": to_json_number(h.cost_usd), "realized_gain": to_json_number(gain)})

    unrealized_q3 = q3.unrealized_gain
    unrealized_q4 = sum((v["value_usd"] - v["cost_usd"] for v in valuation.values()), ZERO)
    change_unrealized = unrealized_q4 - unrealized_q3

    bridge_end = (q3.nav + capital_called - distributions + income - expenses + realized + change_unrealized)
    difference = nav_q4 - bridge_end
    if difference != 0:
        checks.append(f"NAV bridge does not balance: difference {difference}. "
                      "A transaction is probably in the wrong category.")

    nav = {
        "closing_cash": to_json_number(closing_cash),
        "investments_usd": to_json_number(investments),
        "liabilities": to_json_number(liabilities),
        "nav_q4": to_json_number(nav_q4),
        "management_fee": to_json_number(management_fee),
        "bridge": {
            "nav_q3": to_json_number(q3.nav),
            "capital_called": to_json_number(capital_called),
            "distributions": to_json_number(distributions),
            "income": to_json_number(income),
            "expenses": to_json_number(expenses),
            "realized_gain": to_json_number(realized),
            "change_in_unrealized": to_json_number(change_unrealized),
            "nav_q4": to_json_number(bridge_end),
            "difference": to_json_number(difference),
        },
        "detail": {
            "income": {"dividends": to_json_number(dividends), "interest": to_json_number(interest)},
            "expenses": {"management_fee": to_json_number(management_fee),
                         "other_expenses_paid": to_json_number(other_expenses),
                         "bank_charges": to_json_number(bank_charges),
                         "expense_refunds": to_json_number(-refunds),
                         "accrued_expenses_q4": to_json_number(accrued_q4)},
            "accrual_settlements_not_expensed": to_json_number(settlements),
            "liabilities_31_dec": {k: to_json_number(v) for k, v in q3.q4_accruals.items()},
            "sales": sales_detail,
            "unrealized_gain_q3": to_json_number(unrealized_q3),
            "unrealized_gain_q4": to_json_number(unrealized_q4),
            "investments": {k: {"value_usd": to_json_number(v["value_usd"]),
                                "cost_usd": to_json_number(v["cost_usd"]), "basis": v["basis"]}
                            for k, v in sorted(valuation.items())},
            "cash_check": {"opening": to_json_number(opening_cash), "net_movements": to_json_number(movements),
                           "closing": to_json_number(closing_cash)},
        },
        "checks_failed": checks,
    }
    return nav, checks
