"""Deterministic checks on the LLM's categorization.

The LLM does the categorization. This module is the independent "second reader" used to:
  * validate each LLM answer (allowed category, debit/credit direction, LP code format), and
  * spot disagreements, which are recorded on the transaction for a human to look at.
It is also what the offline tests use in place of a live LLM.
"""
from __future__ import annotations

import re

CATEGORIES = (
    "capital_call", "distribution", "management_fee", "investment_purchase", "sale_proceeds",
    "dividend_income", "interest_income", "bank_charges", "expense", "expense_refund",
    "accrual_settlement",
)
CREDIT_CATEGORIES = {"capital_call", "sale_proceeds", "dividend_income", "interest_income", "expense_refund"}
DEBIT_CATEGORIES = set(CATEGORIES) - CREDIT_CATEGORIES
LP_RE = re.compile(r"\bLP(\d{2})\b")
PRIOR_PERIOD_RE = re.compile(r"\b(JUL-SEP|Q3|FY2[01]|PRIOR|ARREARS)\b")


def validate(txn: dict, category: str, lp_code, company) -> list[str]:
    """Return reasons the answer is impossible for this transaction (empty list = acceptable)."""
    errors = []
    if category not in CATEGORIES:
        return [f"unknown category {category!r}"]
    is_credit = txn["credit"] > 0
    if is_credit and category not in CREDIT_CATEGORIES:
        errors.append(f"{category} cannot be a credit")
    if not is_credit and category not in DEBIT_CATEGORIES:
        errors.append(f"{category} cannot be a debit")
    if category in ("capital_call", "distribution"):
        if lp_code is not None and not re.fullmatch(r"LP\d{2}", str(lp_code)):
            errors.append(f"bad lp_code {lp_code!r}")
    if category in ("investment_purchase", "sale_proceeds") and not company:
        errors.append("company is required for purchases and sales")
    return errors


def rule_category(txn: dict) -> str:
    """Keyword rules for this bank's description style. Used as a cross-check, not the answer."""
    d = txn["description"].upper()
    credit = txn["credit"] > 0
    if "INTEREST" in d and credit:
        return "interest_income"
    if "MAINTENANCE FEE" in d or "WIRE CHARGES" in d:
        return "bank_charges"
    if "DISTRIBUTION" in d and not credit:
        return "distribution"
    if credit and ("REFUND" in d or "OVERBILLING" in d):
        return "expense_refund"
    if credit and "DIVIDEND" in d:
        return "dividend_income"
    if credit and re.search(r"\b(SPA|COMPLETION|SALE PROCEEDS)\b", d):
        return "sale_proceeds"
    if credit and (re.search(r"\bCC\d+\b|CALL NO|CAPITAL CALL", d) or LP_RE.search(d)):
        return "capital_call"
    if not credit and ("MGMT FEE" in d or "MANAGEMENT FEE" in d):
        return "management_fee"
    if not credit and re.search(r"\b(SUBSCRIPTION|SERIES [A-Z] SHARES|ACQUISITION OF)\b", d):
        return "investment_purchase"
    if not credit and PRIOR_PERIOD_RE.search(d):
        return "accrual_settlement"
    return "expense_refund" if credit else "expense"


def rule_lp_code(txn: dict):
    m = LP_RE.search(txn["description"].upper())
    return f"LP{m.group(1)}" if m else None


def rule_company(txn: dict):
    d = txn["description"].upper()
    m = re.search(r"OUTWARD WIRE (.+?) (SUBSCRIPTION|SERIES)", d)
    if m:
        return m.group(1).title()
    m = re.search(r"SPA (.+?) COMPLETION", d)
    if m:
        return m.group(1).title()
    return None


def normalize_name(name: str) -> str:
    """Case/punctuation-insensitive key for names, e.g. 'FJORD PAYMENTS AS' == 'Fjord Payments AS'."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())
