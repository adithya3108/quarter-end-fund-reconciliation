"""Agent A - Bank & Ingestion Agent.

Owns the bank statement. Its A2A skill "close-quarter-ingest":
  input : data part {"fund": "GPF", "valuation_date": "2022-12-31",
                     "open_liabilities": [{"description", "amount_usd"}]   (optional context)}
  does  : parse PDF -> check balances -> LLM categorization -> derive purchases/sales ->
          two-turn A2A conversation with Agent B to obtain the Q4 marks
  output: completed Task with artifacts "transactions" and "marks"
"""
from __future__ import annotations

import logging

from agents.bank_agent.categorizer import categorize, derive_purchases_and_sales
from agents.bank_agent.statement_parser import check_balances, parse_statement
from agents.common import a2a
from agents.common.money import to_json_number

log = logging.getLogger("bank_agent")
MAX_HANDSHAKE_ATTEMPTS = 2


def agent_card() -> dict:
    return {
        "name": "GPF Bank & Ingestion Agent",
        "description": "Parses a fund's bank statement PDF, categorizes every transaction with an LLM, "
                       "checks balances in code, and obtains quarter-end marks from the valuation agent.",
        "url": "",  # filled in by A2AServer
        "version": "1.0.0",
        "protocolVersion": a2a.PROTOCOL_VERSION,
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [{
            "id": "close-quarter-ingest",
            "name": "Ingest bank statement and fetch marks",
            "description": "Input data part {fund, valuation_date}. Returns categorized transactions and "
                           "the valuation agent's marks.",
            "tags": ["bank", "categorization", "fund-accounting"],
        }],
    }


class BankAgent:
    def __init__(self, statement_path: str, llm, valuation_agent_url: str, a2a_log_path: str | None):
        self.statement_path = statement_path
        self.llm = llm
        self.valuation_agent_url = valuation_agent_url
        self.a2a_log_path = a2a_log_path
        self.warnings: list[str] = []

    # ---- pure steps (usable without A2A) --------------------------------------------------------
    def ingest(self, open_liabilities: list[dict] | None = None) -> list[dict]:
        stmt = parse_statement(self.statement_path)
        problems = check_balances(stmt)
        if problems:
            raise ValueError("Bank statement does not reconcile: " + "; ".join(problems))
        txns, warnings = categorize(stmt.transactions, self.llm, open_liabilities)
        self.warnings.extend(warnings)
        self.opening_balance = stmt.opening_balance
        self.closing_balance = stmt.closing_balance
        return txns

    def fetch_marks(self, fund: str, valuation_date: str, purchases: list, sales: list) -> dict:
        """The two-turn A2A conversation with Agent B."""
        client = a2a.A2AClient(self.valuation_agent_url, sender="agent_a", receiver="agent_b",
                               log_path=self.a2a_log_path)
        # Turn 1: ask for marks
        task = client.send(a2a.make_message([a2a.data_part({"fund": fund, "valuation_date": valuation_date})]))
        for _ in range(MAX_HANDSHAKE_ATTEMPTS):
            state = task["status"]["state"]
            if state == "completed":
                break
            if state != "input-required":
                raise RuntimeError(f"Agent B returned state {state}: {_status_text(task)}")
            log.info("Agent B asks: %s", _status_text(task))
            # Turn 2: answer from our own output
            answer = a2a.make_message([a2a.data_part({"purchases": purchases, "sales": sales})],
                                      task_id=task["id"], context_id=task["contextId"])
            task = client.send(answer)
        if task["status"]["state"] != "completed":
            raise RuntimeError(f"Agent B did not release marks: {_status_text(task)}")
        return task["artifacts"][0]["parts"][0]["data"]

    # ---- A2A handler -----------------------------------------------------------------------------
    def handle(self, message: dict, task: dict | None) -> dict:
        req = a2a.first_data(message) or {}
        task = a2a.make_task(context_id=message.get("contextId"))
        fund = req.get("fund", "GPF")
        valuation_date = req.get("valuation_date", "2022-12-31")
        try:
            txns = self.ingest(req.get("open_liabilities"))
            purchases, sales = derive_purchases_and_sales(txns)
            marks = self.fetch_marks(fund, valuation_date, purchases, sales)
        except Exception as exc:  # noqa: BLE001 - surface as a failed task
            return a2a.set_status(task, "failed", text=f"{type(exc).__name__}: {exc}")
        task["artifacts"] = [
            {"artifactId": f"transactions-{fund}-{valuation_date}", "name": "transactions",
             "parts": [a2a.data_part({"opening_balance": to_json_number(self.opening_balance),
                                      "closing_balance": to_json_number(self.closing_balance),
                                      "transactions": [serialize_txn(t) for t in txns],
                                      "purchases": purchases, "sales": sales,
                                      "warnings": self.warnings})]},
            {"artifactId": f"marks-{fund}-{valuation_date}", "name": "marks",
             "parts": [a2a.data_part(marks)]},
        ]
        return a2a.set_status(task, "completed", text=f"Categorized {len(txns)} transactions; marks received.")


def serialize_txn(t: dict) -> dict:
    out = {"date": t["date"], "description": t["description"], "reference": t["reference"],
           "debit": to_json_number(t["debit"]), "credit": to_json_number(t["credit"]),
           "balance": to_json_number(t["balance"]), "category": t["category"],
           "lp_code": t["lp_code"], "company": t["company"]}
    if t.get("needs_review"):
        out["needs_review"] = True
    return out


def _status_text(task: dict) -> str:
    msg = task.get("status", {}).get("message")
    return (a2a.first_text(msg) or "") if msg else ""
