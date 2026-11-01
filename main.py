"""Close Q4 2022 for The Greatness PE Fund with two A2A agents.

Usage:  python main.py [PACK_DIR] [--output DIR] [--threshold PCT]
        PACK_DIR defaults to ./pack; results go to ./output.
Environment: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL (required); AGENT_A_PORT, AGENT_B_PORT (optional).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path

import config
from agents.bank_agent.agent import BankAgent, agent_card as card_a
from agents.common import a2a
from agents.common.llm import LLMClient, LLMError
from agents.valuation_agent.agent import ValuationAgent, agent_card as card_b
from agents.valuation_agent.data import load_agent_b_data, load_q3_position

PACK_FILES = {
    "statement": "GPF_Bank_Statement_Q4_2022.pdf",
    "cvp_q3": "CVP_Valuation_Report_Q3_2022.xlsx",
    "nav_q3": "GPF_NAV_Summary_Q3_2022.xlsx",
    "agent_b": "agent_b_data.json",
}


def load_dotenv(path: Path) -> None:
    """Minimal .env reader; real environment variables win over the file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(pack: Path, out: Path, threshold: Decimal, llm=None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    paths = {k: pack / v for k, v in PACK_FILES.items()}
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        print("Missing input files: " + ", ".join(missing), file=sys.stderr)
        return 2
    a2a_log = out / "a2a_log.jsonl"
    orchestrator_log = out / "orchestrator_log.jsonl"
    for f in (a2a_log, orchestrator_log):
        f.unlink(missing_ok=True)

    if llm is None:
        try:
            llm = LLMClient()
        except LLMError as exc:
            print(f"{exc}. Set them in the environment or in a .env file (see .env.example).", file=sys.stderr)
            return 2
    q3 = load_q3_position(str(paths["nav_q3"]), str(paths["cvp_q3"]))
    valuation = ValuationAgent(
        agent_b_data=load_agent_b_data(str(paths["agent_b"])),
        q3_position=q3,
        eurusd_rate=config.EURUSD_RATE, threshold_pct=threshold,
        fx_meta={"rate_date": config.EURUSD_RATE_DATE, "source": config.EURUSD_SOURCE})

    port_a = int(os.environ.get("AGENT_A_PORT", config.DEFAULT_AGENT_A_PORT))
    port_b = int(os.environ.get("AGENT_B_PORT", config.DEFAULT_AGENT_B_PORT))
    server_b = a2a.A2AServer(card_b(), valuation.handle, port=port_b).start()
    bank = BankAgent(str(paths["statement"]), llm, valuation_agent_url=server_b.base_url,
                     a2a_log_path=str(a2a_log))
    server_a = a2a.A2AServer(card_a(), bank.handle, port=port_a).start()
    try:
        # 1. Ask Agent A to ingest the statement; it holds the two-turn conversation with Agent B.
        to_a = a2a.A2AClient(server_a.base_url, "orchestrator", "agent_a", str(orchestrator_log), timeout=900)
        # The payables open at the start of the quarter are context for spotting accrual settlements.
        open_liabilities = [{"description": k, "amount_usd": float(v)} for k, v in q3.liabilities.items()]
        task_a = to_a.send(a2a.make_message([a2a.data_part({"fund": config.FUND_CODE,
                                                            "valuation_date": config.VALUATION_DATE,
                                                            "open_liabilities": open_liabilities})]))
        if task_a["status"]["state"] != "completed":
            print("Agent A failed: " + (a2a.first_text(task_a["status"].get("message", {})) or ""),
                  file=sys.stderr)
            return 1
        artifacts = {a["name"]: a["parts"][0]["data"] for a in task_a["artifacts"]}
        ingest, marks = artifacts["transactions"], artifacts["marks"]
        write_json(out / "transactions.json", ingest["transactions"])
        write_json(out / "marks.json", marks)

        # 2. Ask Agent B to review the marks and compute the NAV from Agent A's ledger.
        to_b = a2a.A2AClient(server_b.base_url, "orchestrator", "agent_b", str(orchestrator_log))
        task_b = to_b.send(a2a.make_message([a2a.data_part({
            "action": "compute_nav", "opening_balance": ingest["opening_balance"],
            "transactions": ingest["transactions"]})]))
        results = {a["name"]: a["parts"][0]["data"] for a in task_b["artifacts"]}
        review, nav = results["review"], results["nav"]
        write_json(out / "review.json", review)
        write_json(out / "nav.json", nav)
        try:
            from workings import write_workings
            write_workings(out / "nav_workings.xlsx", ingest["transactions"], review, nav)
        except Exception as exc:  # optional output; never fail the run for it
            logging.warning("nav_workings.xlsx not written: %s", exc)
    finally:
        server_a.stop()
        server_b.stop()

    print_summary(ingest, review, nav)
    return 1 if nav["checks_failed"] else 0


def print_summary(ingest, review, nav) -> None:
    b = nav["bridge"]
    print(f"Transactions: {len(ingest['transactions'])}")
    for w in ingest.get("warnings", []):
        print(f"  warning: {w}")
    print(f"Flagged: {', '.join(review['flagged_companies'])}  (threshold {review['threshold_pct']}%, "
          f"EUR/USD {review['eurusd_rate']})")
    print(f"Closing cash      {nav['closing_cash']:>18,.2f}")
    print(f"Investments (FV)  {nav['investments_usd']:>18,.2f}")
    print(f"Liabilities       {nav['liabilities']:>18,.2f}")
    print(f"NAV 31 Dec 2022   {nav['nav_q4']:>18,.2f}")
    print("Bridge: " + ", ".join(f"{k} {v:,.2f}" for k, v in b.items()))
    if nav["checks_failed"]:
        print("CHECKS FAILED:\n  " + "\n  ".join(nav["checks_failed"]))
    else:
        print("All checks passed; bridge difference 0.00")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pack", nargs="?", default="./pack", help="input pack directory (default ./pack)")
    parser.add_argument("base", nargs="?", default=None,
                        help="optional repository root; output goes to <base>/output (the brief's "
                             "`python main.py ./pack .`). Ignored when --output is given")
    parser.add_argument("--output", default=None, help="output directory (default ./output)")
    parser.add_argument("--threshold", type=Decimal, default=config.MATERIALITY_THRESHOLD_PCT,
                        help=f"R1 materiality threshold in percent (default {config.MATERIALITY_THRESHOLD_PCT})")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv(Path(".env"))
    out = Path(args.output) if args.output else Path(args.base or ".") / "output"
    return run(Path(args.pack), out, args.threshold)


if __name__ == "__main__":
    sys.exit(main())
