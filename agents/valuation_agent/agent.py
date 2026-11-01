"""Agent B - Valuation & NAV Agent.

Owns agent_b_data.json (CVP's Q4 marks, notes, and the fund's Q4 purchases/sales) and the Q3 files.

A2A skills (both via message/send):
  get-marks   : data {fund, valuation_date} -> Task input-required (asks for Q4 purchases and sales)
                follow-up on the same taskId with {purchases, sales} -> completed with the marks artifact
                if they match our records, otherwise stays input-required with how many matched.
  compute-nav : data {"action": "compute_nav", "opening_balance", "transactions"} -> completed Task
                with artifacts "review" and "nav".
"""
from __future__ import annotations

from decimal import Decimal

from agents.bank_agent.rules import normalize_name
from agents.common import a2a
from agents.common.money import D, cents
from agents.valuation_agent.nav import compute_nav
from agents.valuation_agent.review import review_marks

QUESTION = ("Before I release the Q4 marks, confirm the fund's Q4 purchases and sales of portfolio companies. "
            "Reply on this task with a data part {\"purchases\": [{\"company\", \"date\", \"amount_usd\"}], "
            "\"sales\": [{\"company\", \"date\", \"proceeds_usd\"}]}. Send an empty list if there were none.")


def agent_card() -> dict:
    return {
        "name": "GPF Valuation & NAV Agent",
        "description": "Holds the independent valuer's quarter-end marks. Releases them only after the caller's "
                       "purchases and sales match its records. Reviews marks and computes NAV and the NAV bridge.",
        "url": "",
        "version": "1.0.0",
        "protocolVersion": a2a.PROTOCOL_VERSION,
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {"id": "get-marks", "name": "Release quarter-end marks",
             "description": "Send {fund, valuation_date}. The agent asks for Q4 purchases and sales; answer on the "
                            "same task to receive the marks.",
             "tags": ["valuation", "marks"]},
            {"id": "compute-nav", "name": "Review marks and compute NAV",
             "description": "Send {action: 'compute_nav', opening_balance, transactions}. Returns review and NAV.",
             "tags": ["nav", "review"]},
        ],
    }


def match_trades(theirs: list, ours: list, amount_key: str) -> int:
    """Count items in `theirs` that match one of `ours` on company (case/punctuation-insensitive),
    date and amount (to the cent). Each of ours can be matched once."""
    remaining = list(ours)
    matched = 0
    for item in theirs or []:
        try:
            key = (normalize_name(item["company"]), item["date"], cents(item[amount_key]))
        except (KeyError, TypeError, ArithmeticError):
            continue
        for o in remaining:
            if (normalize_name(o["company"]), o["date"], cents(o[amount_key])) == key:
                remaining.remove(o)
                matched += 1
                break
    return matched


class ValuationAgent:
    def __init__(self, agent_b_data: dict, q3_position, eurusd_rate: Decimal, threshold_pct: Decimal,
                 fx_meta: dict | None = None):
        self.data = agent_b_data
        self.q3 = q3_position
        self.fx_to_usd = {"USD": Decimal("1"), "EUR": D(eurusd_rate)}
        self.eurusd_rate = D(eurusd_rate)
        self.threshold_pct = D(threshold_pct)
        self.fx_meta = fx_meta or {}

    # ---- marks ----------------------------------------------------------------------------------
    def marks_artifact(self) -> dict:
        d = self.data
        return {
            "fund": d["fund"]["name"],
            "valuation_date": d["valuation_date"],
            "marks": [{"company": m["company"], "currency": m["currency"], "fair_value": float(D(m["fair_value"])),
                       "valuation_method": m["valuation_method"], "valuation_date": m["valuation_date"]}
                      for m in d["marks"]],
            "notes": [{"company": n["company"], "note": n["note"]} for n in d["notes"]],
        }

    def _known_fund(self, fund: str) -> bool:
        return fund in (self.data["fund"]["code"], self.data["fund"]["name"])

    def handle(self, message: dict, task: dict | None) -> dict:
        req = a2a.first_data(message) or {}
        if task is None and req.get("action") == "compute_nav":
            return self._handle_compute_nav(message, req)
        if task is None:
            task = a2a.make_task(context_id=message.get("contextId"))
            if not self._known_fund(req.get("fund", "")):
                return a2a.set_status(task, "rejected", text=f"Unknown fund {req.get('fund')!r}")
            if req.get("valuation_date") != self.data["valuation_date"]:
                return a2a.set_status(task, "rejected",
                                      text=f"No marks for valuation date {req.get('valuation_date')!r}; "
                                           f"I hold {self.data['valuation_date']}")
            return a2a.set_status(task, "input-required", text=QUESTION)

        # second turn: verify purchases and sales
        if "purchases" not in req or "sales" not in req:
            return a2a.set_status(task, "input-required",
                                  text="Missing 'purchases' and/or 'sales'. " + QUESTION)
        ours_p, ours_s = self.data.get("purchases", []), self.data.get("sales", [])
        mp = match_trades(req["purchases"], ours_p, "amount_usd")
        ms = match_trades(req["sales"], ours_s, "proceeds_usd")
        total_ours = len(ours_p) + len(ours_s)
        total_theirs = len(req["purchases"] or []) + len(req["sales"] or [])
        if mp + ms == total_ours and total_theirs == total_ours:
            task["artifacts"] = [{"artifactId": f"marks-{self.data['fund']['code']}-{self.data['valuation_date']}",
                                  "name": "marks", "parts": [a2a.data_part(self.marks_artifact())]}]
            return a2a.set_status(task, "completed",
                                  text=f"Purchases and sales match ({mp + ms} of {total_ours}). Marks released.")
        return a2a.set_status(
            task, "input-required",
            text=(f"{mp + ms} of {total_ours} items matched my records (purchases {mp}/{len(ours_p)}, "
                  f"sales {ms}/{len(ours_s)}); you sent {total_theirs}. Check company, date and amount and "
                  f"send again."),
            data={"matched": mp + ms, "expected": total_ours, "received": total_theirs})

    # ---- review + NAV ---------------------------------------------------------------------------
    def run_review_and_nav(self, transactions: list, opening_balance) -> tuple[dict, dict, list]:
        txns = [t for t in transactions]
        # Purchases/sales for holdings come from our own records, which Agent A has just matched.
        entries, valuation = review_marks(self.q3.holdings, self.data["marks"], self.data["notes"],
                                          self.data.get("purchases", []), self.data.get("sales", []),
                                          self.fx_to_usd, self.threshold_pct)
        review = {
            "valuation_date": self.data["valuation_date"],
            "threshold_pct": float(self.threshold_pct),
            "threshold_measured_in": "company's own currency",
            "eurusd_rate": float(self.eurusd_rate),
            **{f"eurusd_{k}": v for k, v in self.fx_meta.items()},
            "flagged_companies": [e["company"] for e in entries if e["flags"]],
            "companies": entries,
        }
        nav, checks = compute_nav(txns, opening_balance, self.q3, valuation)
        return review, nav, checks

    def _handle_compute_nav(self, message: dict, req: dict) -> dict:
        task = a2a.make_task(context_id=message.get("contextId"))
        review, nav, checks = self.run_review_and_nav(req["transactions"], req["opening_balance"])
        task["artifacts"] = [
            {"artifactId": "review", "name": "review", "parts": [a2a.data_part(review)]},
            {"artifactId": "nav", "name": "nav", "parts": [a2a.data_part(nav)]},
        ]
        state = "completed"
        text = (f"NAV at {self.data['valuation_date']}: {nav['nav_q4']:,.2f}; bridge difference "
                f"{nav['bridge']['difference']:.2f}")
        if checks:
            text += ". Checks failed: " + " | ".join(checks)
        return a2a.set_status(task, state, text=text)
