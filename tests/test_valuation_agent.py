"""Review rules R1-R4 and the NAV bridge."""
import copy
from decimal import Decimal

from agents.valuation_agent.nav import compute_nav
from agents.valuation_agent.review import review_marks

FX = {"USD": Decimal("1"), "EUR": Decimal("1.0666")}


def _review(q3, data, threshold="15", marks=None):
    return review_marks(q3.holdings, marks if marks is not None else data["marks"], data["notes"],
                        data["purchases"], data["sales"], FX, Decimal(threshold))


def _by(entries):
    return {e["company"]: e for e in entries}


def test_q3_files_tie_out(q3):
    assert q3.nav == Decimal("160224800")
    assert q3.total_liabilities == Decimal("185000")
    assert q3.unrealized_gain == Decimal("23775800")
    assert q3.holdings["Ember Retail GmbH"].cost_usd == Decimal("14184000")  # not re-converted


def test_flags_at_15_percent(q3, agent_b_data):
    entries, valuation = _review(q3, agent_b_data)
    e = _by(entries)
    assert e["Bluetarn Software Inc"]["flags"] == ["R1"]
    assert e["Cedar Health Holdings"]["flags"] == ["R1"]
    assert e["Harbor Analytics Inc"]["flags"] == ["R1"]
    assert e["Delta Agritech Ltd"]["flags"] == ["R2"]
    assert e["Ionmere Materials Ltd"]["flags"] == ["R3"]
    assert e["Fjord Payments AS"]["flags"] == [] and not e["Fjord Payments AS"]["held_at_31_dec"]
    assert "Fjord Payments AS" not in valuation
    assert valuation["Ionmere Materials Ltd"]["value_usd"] == Decimal("14000000")


def test_fx_move_is_not_a_value_change(q3, agent_b_data):
    ember = _by(_review(q3, agent_b_data, threshold="5")[0])["Ember Retail GmbH"]
    # EUR value fell 2.96%, USD value rose 6.18% only because EUR/USD moved: not flagged at 5%
    assert ember["change_pct"] == -2.96 and ember["change_pct_usd"] == 6.18
    assert ember["flags"] == []
    assert ember["q4_value_usd_in_nav"] == 13972460.0


def test_threshold_sensitivity(q3, agent_b_data):
    flagged = lambda t: {c for c, e in _by(_review(q3, agent_b_data, t)[0]).items() if "R1" in e["flags"]}
    assert flagged("20") == {"Bluetarn Software Inc", "Cedar Health Holdings"}
    assert "Harbor Analytics Inc" in flagged("15")


def test_r4_mark_for_company_not_held(q3, agent_b_data):
    marks = agent_b_data["marks"] + [{"company": "Fjord Payments AS", "currency": "USD", "fair_value": "1",
                                      "valuation_method": "Recent transaction", "valuation_date": "2022-12-31"}]
    entries, valuation = _review(q3, agent_b_data, marks=marks)
    assert _by(entries)["Fjord Payments AS"]["flags"] == ["R4"]
    assert "Fjord Payments AS" not in valuation


def _txns(statement):
    from agents.bank_agent.categorizer import categorize
    from tests.fakes import RuleBasedLLM
    txns, _ = categorize(statement.transactions, RuleBasedLLM())
    return txns


def test_nav_and_bridge_balance_to_the_cent(statement, q3, agent_b_data):
    _, valuation = _review(q3, agent_b_data)
    nav, checks = compute_nav(_txns(statement), statement.opening_balance, q3, valuation)
    assert checks == []
    assert nav["closing_cash"] == 23896579.25
    assert nav["investments_usd"] == 146062460.00
    assert nav["liabilities"] == 195000.00
    assert nav["nav_q4"] == 169764039.25
    b = nav["bridge"]
    assert (b["capital_called"], b["distributions"], b["income"]) == (20000000.0, 22500000.0, 459429.25)
    assert (b["expenses"], b["realized_gain"], b["change_in_unrealized"]) == (1522850.0, 9000000.0, 4102660.0)
    assert b["difference"] == 0.0 and b["nav_q4"] == nav["nav_q4"]


def test_miscategorized_accrual_breaks_the_bridge(statement, q3, agent_b_data):
    txns = copy.deepcopy(_txns(statement))
    admin = next(t for t in txns if "ADMIN SVCS JUL-SEP" in t["description"])
    admin["category"] = "expense"
    _, valuation = _review(q3, agent_b_data)
    nav, checks = compute_nav(txns, statement.opening_balance, q3, valuation)
    assert nav["bridge"]["difference"] == 65000.0
    assert any("does not balance" in c for c in checks)
