"""Categorize bank transactions with an LLM, then check every answer in code."""
from __future__ import annotations

import json
import logging

from agents.bank_agent import rules

log = logging.getLogger("bank_agent")

SYSTEM_PROMPT = """You are a fund accountant categorizing the bank transactions of a private equity fund \
(The Greatness PE Fund, L.P., "GPF") for the quarter ended 31 December 2022.

Put each transaction in exactly one category:
- capital_call: money received from a limited partner (LP) paying a capital call. Extract lp_code (LP01-LP10).
- distribution: money paid out to an LP. Extract lp_code.
- management_fee: the fund's management fee paid to its manager/adviser.
- investment_purchase: money paid to buy shares in a portfolio company. Extract company (the company invested in).
- sale_proceeds: money received from selling a portfolio company. Extract company (the company SOLD, not the buyer).
- dividend_income: dividend received from a portfolio company.
- interest_income: bank interest received.
- bank_charges: bank fees (account maintenance, wire charges).
- expense: any other fund expense paid in this quarter (legal, insurance, etc.), even if it covers a later period.
- expense_refund: a refund of an expense previously paid.
- accrual_settlement: payment of a liability that was already accrued at the start of the quarter \
(30 Sep 2022), i.e. a bill for services in a PRIOR period (e.g. Q3 Jul-Sep administration fees, a prior \
financial year's audit fee).

Rules:
- Wires can wrap and use abbreviations. If an LP's code is not in the text, find the same LP elsewhere in \
the statement (e.g. its distribution line) and use that code.
- lp_code is null unless the category is capital_call or distribution. company is null unless the category is \
investment_purchase, sale_proceeds or dividend_income. Write company names in normal case, e.g. "Acme Widgets Ltd".
- Reply with JSON only: a list of objects {"i": <index>, "category": ..., "lp_code": ..., "company": ...}, \
one per transaction, in the same order. No prose."""


def _row_for_prompt(i: int, t: dict) -> dict:
    return {"i": i, "date": t["date"], "description": t["description"], "reference": t["reference"],
            "debit": str(t["debit"]), "credit": str(t["credit"])}


def categorize(transactions: list[dict], llm, open_liabilities: list[dict] | None = None,
               batch_size: int = 40) -> tuple[list[dict], list[str]]:
    """Return (categorized transactions, warnings).

    Every transaction is sent to the LLM with the whole statement as context (so it can match an LP
    named without a code to the same LP elsewhere). Answers that fail validation are re-asked once,
    individually, with the reason. Anything still invalid raises: we never silently guess.
    """
    context = [_row_for_prompt(i, t) for i, t in enumerate(transactions)]
    if open_liabilities:
        system = SYSTEM_PROMPT + ("\n\nLiabilities already accrued at the start of the quarter (a payment "
                                  "settling one of these is accrual_settlement):\n" + json.dumps(open_liabilities))
    else:
        system = SYSTEM_PROMPT
    answers: dict[int, dict] = {}
    for start in range(0, len(transactions), batch_size):
        idx = list(range(start, min(start + batch_size, len(transactions))))
        user = ("Full statement for context:\n" + json.dumps(context) +
                f"\n\nCategorize transactions with i in {idx[0]}..{idx[-1]} only.")
        for a in _as_list(llm.chat_json(system, user)):
            if isinstance(a, dict) and isinstance(a.get("i"), int) and a["i"] in idx:
                answers[a["i"]] = a

    warnings: list[str] = []
    out = []
    for i, t in enumerate(transactions):
        a = answers.get(i)
        errors = ["no answer from LLM"] if a is None else rules.validate(t, *_fields(a))
        if errors:
            a = _retry_one(llm, system, context, i, errors)
            errors = ["no answer from LLM"] if a is None else rules.validate(t, *_fields(a))
            if errors:
                raise ValueError(f"Transaction {i} ({t['date']} {t['description'][:40]}): {errors}")
        category, lp_code, company = _fields(a)
        out.append({**t, "category": category, "lp_code": lp_code, "company": company,
                    "classified_by": "llm"})

    _fill_missing_lp_codes(out, warnings)
    for t in out:
        expected = rules.rule_category(t)
        if expected != t["category"]:
            t["needs_review"] = True
            warnings.append(f"{t['date']} {t['description'][:50]!r}: LLM said {t['category']}, "
                            f"keyword rules say {expected}")
    return out, warnings


def _fields(a: dict):
    category = (a.get("category") or "").strip()
    lp = a.get("lp_code") or None
    company = a.get("company") or None
    if category not in ("capital_call", "distribution"):
        lp = None
    if category not in ("investment_purchase", "sale_proceeds", "dividend_income"):
        company = None
    return category, (lp.upper().strip() if isinstance(lp, str) else lp), \
        (company.strip() if isinstance(company, str) else company)


def _retry_one(llm, system, context, i, errors):
    user = ("Full statement for context:\n" + json.dumps(context) +
            f"\n\nYour previous answer for transaction i={i} was rejected: {errors}. "
            f"Categorize transaction i={i} only.")
    try:
        for a in _as_list(llm.chat_json(system, user)):
            if isinstance(a, dict) and a.get("i") == i:
                return a
    except Exception as exc:  # noqa: BLE001 - report as a failed retry
        log.warning("retry for %s failed: %s", i, exc)
    return None


def _as_list(parsed):
    if isinstance(parsed, dict):
        for key in ("transactions", "results", "items", "data"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
        return [parsed]
    return parsed if isinstance(parsed, list) else []


def _fill_missing_lp_codes(txns: list[dict], warnings: list[str]) -> None:
    """Belt and braces: if a capital call/distribution has no lp_code, look for the same counterparty
    on another line of the statement that does carry a code (matched on shared name words)."""
    def name_words(desc: str) -> set:
        stop = {"WIRE", "IN", "ORG", "REF", "GPF", "TO", "OUTWARD", "DISTRIBUTION", "INCOMING", "TT",
                "BNF", "GREATNESS", "PE", "FUND", "CALL", "NO", "CC07", "PART", "OF", "BAL"}
        tokens = {w.strip("():/,.").upper() for w in desc.replace(":", " ").split()}
        return {w for w in tokens if len(w) > 2 and w not in stop and not w[0].isdigit()
                and not rules.LP_RE.fullmatch(w)}

    known = [(name_words(t["description"]), t["lp_code"]) for t in txns if t["lp_code"]]
    for t in txns:
        if t["category"] in ("capital_call", "distribution") and not t["lp_code"]:
            words = name_words(t["description"])
            best = max(known, key=lambda k: len(k[0] & words), default=(set(), None))
            if best[1] and len(best[0] & words) >= 1:
                t["lp_code"] = best[1]
                warnings.append(f"{t['date']}: lp_code {best[1]} inferred from another line of the statement")
            else:
                raise ValueError(f"No LP code for {t['category']} on {t['date']}: {t['description']}")


def derive_purchases_and_sales(txns: list[dict]) -> tuple[list[dict], list[dict]]:
    purchases = [{"company": t["company"], "date": t["date"], "amount_usd": float(t["debit"])}
                 for t in txns if t["category"] == "investment_purchase"]
    sales = [{"company": t["company"], "date": t["date"], "proceeds_usd": float(t["credit"])}
             for t in txns if t["category"] == "sale_proceeds"]
    return purchases, sales
