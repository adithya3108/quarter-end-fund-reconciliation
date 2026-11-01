"""Load Agent B's inputs: CVP's Q4 data (JSON) and the Q3 Excel files.

Cells are found by their labels rather than fixed cell addresses, and SUM formulas are recomputed
from the rows rather than trusting cached values, so a re-saved workbook still loads.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

import openpyxl

from agents.common.money import D


@dataclass
class Q3Holding:
    company: str
    currency: str
    cost_local: Decimal
    fair_value_local: Decimal
    fx_rate: Decimal
    fair_value_usd: Decimal
    method: str
    cost_usd: Decimal = Decimal("0")


@dataclass
class Q3Position:
    cash: Decimal
    investments_usd: Decimal
    liabilities: dict            # label -> amount
    nav: Decimal
    holdings: dict = field(default_factory=dict)   # company -> Q3Holding
    commitments: dict = field(default_factory=dict)  # LP code -> amount
    q4_accruals: dict = field(default_factory=dict)  # label -> amount

    @property
    def total_liabilities(self) -> Decimal:
        return sum(self.liabilities.values(), Decimal("0"))

    @property
    def unrealized_gain(self) -> Decimal:
        return sum((h.fair_value_usd - h.cost_usd for h in self.holdings.values()), Decimal("0"))


def _rows(ws):
    return [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_cvp_q3(path: str) -> dict:
    wb = openpyxl.load_workbook(path, data_only=False)
    ws = wb.worksheets[0]
    holdings, in_table = {}, False
    for r in _rows(ws):
        if r[0] == "Portfolio company":
            in_table = True
            continue
        if in_table:
            if not _is_number(r[2]):  # 'Total (USD)' or notes
                break
            holdings[r[0]] = Q3Holding(company=r[0], currency=r[1], cost_local=D(r[2]),
                                       fair_value_local=D(r[3]), fx_rate=D(r[4]),
                                       fair_value_usd=D(r[5]), method=r[6])
    return holdings


def load_q3_position(nav_summary_path: str, cvp_q3_path: str) -> Q3Position:
    holdings = load_cvp_q3(cvp_q3_path)
    wb = openpyxl.load_workbook(nav_summary_path, data_only=False)
    rows = _rows(wb.worksheets[0])
    labels = {str(r[0]): r for r in rows if r[0] is not None}

    def find(prefix):
        for k, r in labels.items():
            if k.startswith(prefix):
                return D(r[1])
        raise KeyError(f"Row starting with {prefix!r} not found in NAV summary")

    liabilities, section = {}, None
    for r in rows:
        label = str(r[0])
        if label == "Liabilities":
            section = "liab"
            continue
        if section == "liab":
            if label.startswith("Total liabilities"):
                break
            liabilities[label] = D(r[1])

    # cost per company in USD (keeps Ember at its historical USD cost; never re-converted)
    in_table = False
    for r in rows:
        if r[0] == "Portfolio company":
            in_table = True
            continue
        if in_table:
            if r[0] == "Total" or not _is_number(r[1]):
                break
            if r[0] not in holdings:
                raise ValueError(f"{r[0]} is in the NAV summary but not in the CVP Q3 report")
            holdings[r[0]].cost_usd = D(r[1])
            if D(r[2]) != holdings[r[0]].fair_value_usd:
                raise ValueError(f"Q3 fair value mismatch for {r[0]}: NAV summary {r[2]} vs CVP")

    commitments = {r[1]: D(r[2]) for r in rows
                   if isinstance(r[1], str) and r[1].startswith("LP") and _is_number(r[2])}

    accruals = {}
    for r in _rows(wb.worksheets[1]):
        if _is_number(r[1]) and not str(r[0]).startswith("Total"):
            accruals[str(r[0])] = D(r[1])

    pos = Q3Position(cash=find("Cash at bank"), investments_usd=find("Investments at fair value"),
                     liabilities=liabilities, nav=find("Net asset value"), holdings=holdings,
                     commitments=commitments, q4_accruals=accruals)
    # The Q3 numbers must tie out before we build on them.
    if sum((h.fair_value_usd for h in holdings.values()), Decimal("0")) != pos.investments_usd:
        raise ValueError("Q3 investments do not equal the sum of company fair values")
    if pos.cash + pos.investments_usd - pos.total_liabilities != pos.nav:
        raise ValueError("Q3 NAV does not equal cash + investments - liabilities")
    return pos


def load_agent_b_data(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
