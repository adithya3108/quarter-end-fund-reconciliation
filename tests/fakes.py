"""Offline stand-ins for the LLM. Tests never call a network service."""
import json
import re
from decimal import Decimal

from agents.bank_agent import rules


class RuleBasedLLM:
    """Answers the categorizer's prompt using the keyword rules, in the same JSON shape a model returns.

    `overrides` lets a test force a wrong answer for index i, e.g. {1: "expense"}.
    """

    def __init__(self, overrides=None, fence=False):
        self.overrides = overrides or {}
        self.fence = fence
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        context = json.loads(user.split("Full statement for context:\n", 1)[1].split("\n\n", 1)[0])
        m = re.search(r"i in (\d+)\.\.(\d+)", user)
        wanted = range(int(m.group(1)), int(m.group(2)) + 1) if m else \
            [int(re.search(r"transaction i=(\d+) only", user).group(1))]
        out = []
        for i in wanted:
            row = context[i]
            t = {"description": row["description"], "debit": Decimal(row["debit"]), "credit": Decimal(row["credit"])}
            cat = self.overrides.pop(i, None) if "rejected" not in user else None
            cat = cat or rules.rule_category(t)
            lp = rules.rule_lp_code(t) if cat in ("capital_call", "distribution") else None
            company = rules.rule_company(t) if cat in ("investment_purchase", "sale_proceeds") else None
            if cat == "dividend_income":
                company = "Aurora Logistics Pvt Ltd"
            out.append({"i": i, "category": cat, "lp_code": lp, "company": company})
        text = json.dumps(out)
        return f"```json\n{text}\n```" if self.fence else text

    def chat_json(self, system, user):
        from agents.common.llm import extract_json
        return extract_json(self.chat(system, user))
