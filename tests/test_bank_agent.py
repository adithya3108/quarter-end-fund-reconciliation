"""PDF parsing, balance checks and LLM-answer validation (with a fake LLM)."""
import copy
from decimal import Decimal

import pytest

from agents.bank_agent.categorizer import categorize, derive_purchases_and_sales
from agents.bank_agent.statement_parser import check_balances
from tests.fakes import RuleBasedLLM


def test_parses_every_row_and_wrapped_descriptions(statement):
    assert len(statement.transactions) == 39
    assert statement.opening_balance == Decimal("12450000.00")
    assert statement.closing_balance == Decimal("23896579.25")
    first = statement.transactions[0]
    assert first["description"] == ("OUTWARD WIRE TO GREATNESS CAPITAL ADVISORS LLC REF MGMT FEE Q4-22 PER LPA 8.1")
    assert first["reference"] == "OW-221003-01" and first["debit"] == Decimal("1250000.00")


def test_rows_without_reference_and_across_page_break(statement):
    lp02 = statement.transactions[4]
    assert lp02["reference"] is None and lp02["credit"] == Decimal("3200000.00") and lp02["debit"] == 0
    # first row on page 2 (a distribution) keeps its two-line description
    marlow = [t for t in statement.transactions if "MARLOW FOF II (LP08)" in t["description"]]
    assert len(marlow) == 1 and marlow[0]["debit"] == Decimal("1350000.00")


def test_balances_reconcile(statement):
    assert check_balances(statement) == []


def test_balance_check_catches_a_misread_amount(statement):
    broken = copy.deepcopy(statement)
    broken.transactions[5]["credit"] = Decimal("400000.00")  # misread 4,000,000.00
    problems = check_balances(broken)
    assert any("running balance" in p for p in problems)


def test_categorize_with_fake_llm(statement):
    txns, warnings = categorize(statement.transactions, RuleBasedLLM(fence=True))
    cats = [t["category"] for t in txns]
    assert cats.count("capital_call") == 11 and cats.count("distribution") == 10
    assert cats.count("accrual_settlement") == 2
    mehta = next(t for t in txns if "MEHTA FAM TRST" in t["description"])
    assert mehta["lp_code"] == "LP10"  # inferred from the LP10 distribution line
    purchases, sales = derive_purchases_and_sales(txns)
    assert purchases == [{"company": "Ionmere Materials Ltd", "date": "2022-12-15", "amount_usd": 14000000.0}]
    assert sales[0]["proceeds_usd"] == 29000000.0 and sales[0]["date"] == "2022-11-18"


def test_invalid_llm_answer_is_retried(statement):
    llm = RuleBasedLLM(overrides={2: "management_fee"})  # a credit (dividend) labelled as a fee
    txns, _ = categorize(statement.transactions, llm)
    assert txns[2]["category"] == "dividend_income"
    assert llm.calls == 2  # 1 batch + 1 retry


def test_plausible_but_different_answer_is_flagged_for_review(statement):
    llm = RuleBasedLLM(overrides={1: "expense"})  # Q3 admin fee treated as a Q4 expense
    txns, warnings = categorize(statement.transactions, llm)
    assert txns[1]["category"] == "expense" and txns[1]["needs_review"]
    assert any("keyword rules say accrual_settlement" in w for w in warnings)


def test_llm_that_never_answers_fails_loudly(statement):
    class Silent:
        def chat_json(self, s, u):
            return []
    with pytest.raises(ValueError):
        categorize(statement.transactions[:3], Silent())
