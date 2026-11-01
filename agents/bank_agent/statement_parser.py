"""Parse the bank statement PDF into transactions, using word positions rather than raw text.

Why positions: descriptions wrap onto several lines and some rows have no reference, so a plain
text split cannot tell a credit from a debit. The column headers (Date, Description, Reference,
Debit, Credit, Balance) give the x-boundaries on each page; amounts are right-aligned, so each
number is assigned to a column by its right edge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import pdfplumber

from agents.common.money import D

DATE_RE = re.compile(r"^\d{2}$")
MONTH_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$")
AMOUNT_RE = re.compile(r"^-?[\d,]+\.\d{2}$")
HEADERS = ("Date", "Description", "Reference", "Debit", "Credit", "Balance")
MAX_WRAP_GAP = 13.0  # points between a row's lines; larger gaps mean we left the table


@dataclass
class Row:
    date: str
    description_lines: list = field(default_factory=list)
    reference: str | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None
    balance: Decimal | None = None

    @property
    def description(self) -> str:
        return " ".join(self.description_lines).strip()


@dataclass
class Statement:
    opening_balance: Decimal
    transactions: list            # list[dict], excludes the BALANCE BROUGHT FORWARD line
    closing_balance: Decimal
    total_debits: Decimal | None
    total_credits: Decimal | None


def _group_lines(words, tol=2.0):
    lines = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0] - w["top"]) <= tol:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    return [(top, sorted(ws, key=lambda w: w["x0"])) for top, ws in lines]


def _columns(words):
    """Find the header row on this page and return the column boundaries."""
    heads = {}
    for w in words:
        if w["text"] in HEADERS and w["text"] not in heads:
            heads[w["text"]] = w
    if not all(h in heads for h in HEADERS):
        return None
    return {
        "header_top": heads["Date"]["top"],
        "desc_x0": heads["Description"]["x0"] - 2,
        "ref_x0": heads["Reference"]["x0"] - 2,
        # amounts are right-aligned: a number ends just before the next column header starts
        "credit_x0": heads["Credit"]["x0"],
        "balance_x0": heads["Balance"]["x0"],
        "debit_x0": heads["Debit"]["x0"] - 25,
    }


def parse_statement(pdf_path: str) -> Statement:
    rows: list[Row] = []
    totals = None
    current: Row | None = None
    last_top = None
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            cols = _columns(words)
            if cols is None:
                continue
            last_top = None
            for top, ws in _group_lines(words):
                if top <= cols["header_top"] + 1:
                    continue
                texts = [w["text"] for w in ws]
                line_text = " ".join(texts)
                if line_text.startswith("TOTAL DEBITS"):
                    amounts = [D(t) for t in texts if AMOUNT_RE.match(t)]
                    totals = amounts[:2] if len(amounts) >= 2 else None
                    current = None
                    break
                is_new_row = (len(ws) >= 3 and DATE_RE.match(texts[0]) and MONTH_RE.match(texts[1])
                              and re.match(r"^\d{4}$", texts[2]) and ws[0]["x0"] < cols["desc_x0"])
                if is_new_row:
                    date = datetime.strptime(" ".join(texts[:3]), "%d %b %Y").strftime("%Y-%m-%d")
                    current = Row(date=date)
                    rows.append(current)
                    rest = ws[3:]
                elif current is not None and last_top is not None and top - last_top <= MAX_WRAP_GAP:
                    rest = ws  # wrapped description line
                else:
                    current = None
                    last_top = None
                    continue
                desc_words = []
                for w in rest:
                    t = w["text"]
                    if AMOUNT_RE.match(t) and w["x0"] >= cols["debit_x0"]:
                        value = D(t)
                        if w["x1"] < cols["credit_x0"]:
                            current.debit = value
                        elif w["x1"] < cols["balance_x0"]:
                            current.credit = value
                        else:
                            current.balance = value
                    elif w["x0"] >= cols["ref_x0"]:
                        current.reference = t
                    else:
                        desc_words.append(t)
                if desc_words:
                    current.description_lines.append(" ".join(desc_words))
                last_top = top

    if not rows or "BROUGHT FORWARD" not in rows[0].description:
        raise ValueError("Statement does not start with a BALANCE BROUGHT FORWARD line")
    opening = rows[0].balance
    txns = []
    for r in rows[1:]:
        txns.append({
            "date": r.date,
            "description": r.description,
            "reference": r.reference,
            "debit": r.debit if r.debit is not None else Decimal("0"),
            "credit": r.credit if r.credit is not None else Decimal("0"),
            "balance": r.balance,
        })
    return Statement(opening_balance=opening, transactions=txns,
                     closing_balance=txns[-1]["balance"] if txns else opening,
                     total_debits=totals[0] if totals else None,
                     total_credits=totals[1] if totals else None)


def check_balances(stmt: Statement) -> list[str]:
    """Return a list of problems; empty list means the statement reconciles to the cent."""
    problems = []
    running = stmt.opening_balance
    for i, t in enumerate(stmt.transactions, start=1):
        if t["balance"] is None:
            problems.append(f"row {i} ({t['date']}): no running balance")
            continue
        if (t["debit"] > 0) == (t["credit"] > 0):
            problems.append(f"row {i} ({t['date']}): expected exactly one of debit/credit")
        running = running - t["debit"] + t["credit"]
        if running != t["balance"]:
            problems.append(f"row {i} ({t['date']}): running balance {running} != printed {t['balance']}")
            running = t["balance"]  # re-sync so one error is reported once
    debits = sum((t["debit"] for t in stmt.transactions), Decimal("0"))
    credits = sum((t["credit"] for t in stmt.transactions), Decimal("0"))
    if stmt.opening_balance - debits + credits != stmt.closing_balance:
        problems.append("opening balance + movements != closing balance")
    if stmt.total_debits is not None and debits != stmt.total_debits:
        problems.append(f"sum of debits {debits} != printed total {stmt.total_debits}")
    if stmt.total_credits is not None and credits != stmt.total_credits:
        problems.append(f"sum of credits {credits} != printed total {stmt.total_credits}")
    return problems
