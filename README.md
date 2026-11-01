# GPF Q4 2022 close: two A2A agents

This project turns the Q4 2022 bank statement, the Q3 files and CVP's Q4 marks into a checked NAV at 31 December 2022, with a NAV bridge that balances to the cent.

## Run it

```bash
python -m pip install -r requirements.txt
cp .env.example .env              # then set LLM_API_KEY (or export the three LLM_* variables)
python main.py ./pack             # reads ./pack, writes ./output (`python main.py ./pack .` also works)
```

| Argument / variable | Default | Meaning |
|---|---|---|
| `pack` (positional) | `./pack` | Input directory |
| `base` (optional 2nd positional) | `.` | Repo root; output goes to `<base>/output` |
| `--output DIR` | `<base>/output` | Output directory |
| `--threshold PCT` | `15` | R1 materiality threshold, in % |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | none (required) | Any OpenAI-compatible endpoint. Read from the environment or `.env`; real environment variables take precedence over `.env`. |
| `AGENT_A_PORT`, `AGENT_B_PORT` | `9101`, `9102` | Agent ports (`0` picks a free port). Both agents stop when the run ends. |

The exit code is 0 when every check passes, 1 when a check fails (for example, the bridge doesn't balance), and 2 for missing inputs or missing LLM settings.

Tests run offline, with no LLM and no network beyond localhost, and need no API key: `python -m pytest` (25 tests)

## How it works

```
main.py (orchestrator)
  │ message/send {fund, valuation_date, open_liabilities}
  ▼
Agent A: Bank & Ingestion                     Agent B: Valuation & NAV
  parse PDF by word position                    owns agent_b_data.json + Q3 xlsx
  check every running balance  ──turn 1──▶      {fund, valuation_date}
  LLM categorizes every row    ◀────────        Task input-required: "send Q4 purchases and sales"
  code validates every answer  ──turn 2──▶      {purchases, sales} on same taskId/contextId
  derive purchases and sales   ◀────────        completed + artifact marks-GPF-2022-12-31
  │ returns transactions + marks                 (or input-required: "1 of 2 items matched")
  ▼
main.py ── message/send {action: compute_nav, transactions} ──▶ Agent B
            review R1–R4, NAV, bridge  ◀── completed: artifacts review + nav
```

| Path | What it is |
|---|---|
| `agents/common/a2a.py` | A2A v0.3 over JSON-RPC 2.0, standard library only: agent card, `message/send`, in-memory tasks, a client that logs every request and response |
| `agents/common/llm.py` | OpenAI-compatible `/chat/completions` client (retries on 429/5xx, tolerant JSON extraction) |
| `agents/bank_agent/statement_parser.py` | PDF → rows using column positions; balance checks |
| `agents/bank_agent/categorizer.py` | LLM prompt, per-answer validation and retry, LP-code backfill, purchases/sales |
| `agents/bank_agent/rules.py` | Allowed categories and directions; keyword cross-check; name normalization |
| `agents/valuation_agent/data.py` | Loads the Q3 Excel files by label and checks that they tie out |
| `agents/valuation_agent/review.py` | Holdings at 31 Dec, R1–R4, value per stake in USD |
| `agents/valuation_agent/nav.py` | NAV, bridge, accrual-settlement matching, all in `Decimal` |
| `config.py` | The judgment calls: EUR/USD rate and date, threshold |
| `workings.py` | Optional `nav_workings.xlsx` with live formulas (the bridge re-adds in Excel) |

Each agent takes plain inputs: a file path, an LLM client and a peer URL for A; the Q3 position, CVP data, FX rate and threshold for B. Neither agent hard-codes GPF, so either could be pointed at another fund's files.

## Outputs (`./output`)

`transactions.json`, `a2a_log.jsonl` (A↔B only), `marks.json`, `review.json`, `nav.json` and `nav_workings.xlsx`, plus `orchestrator_log.jsonl` (the orchestrator's own A2A calls). `nav.json` also includes `management_fee`, a `detail` breakdown and a `checks_failed` list.

## Taking it from prototype to product

- **Human review step:** a `needs_review` row, an R-flag or a failed check should go to a reviewer's queue, and the run should wait for sign-off instead of only printing.
- **Per-bank parsing profiles:** column detection is generic, but the keyword cross-check is written for this bank's wording. Each bank and fund should get its own profile, along with a small set of labelled transactions for checking accuracy.
- **A2A hardening:** authentication on both agents (signed agent cards, or OAuth as declared in the card's `securitySchemes`); `tasks/get` and cancellation; tasks persisted to a store instead of memory; idempotent message IDs.
- **FX from a source, not config:** fetch and store ECB rates with their source and timestamp, and support more than one currency.
- **Accounting model:** a proper ledger (prepayments, accruals by period, follow-on investments, partial sales, management-fee offsets), and per-LP capital account allocation.
- **Audit trail:** store the prompt, model, raw response and validation result for every LLM decision.

## What is missing or weak

- The keyword cross-check is tuned to this statement's wording. On another bank it would produce many disagreements (which are surfaced, not hidden) until it is extended.
- Only `message/send` is implemented. There is no streaming, `tasks/get`, push notifications or authentication. Tasks live in memory and disappear when the run ends.
- If Agent B rejects the purchases and sales, Agent A shows the reason and resends once, but it has no way of correcting its own lists automatically. The run then fails with a clear message.
- Only USD and EUR are supported. The EUR rate is a constant in `config.py` with its source cited, not fetched.
- Partial sales and follow-on investments are only lightly handled: a follow-on adds to cost, and a sale is assumed to be the whole stake.
- Free-tier LLM rate limits are handled by retrying (up to five attempts, honouring `retry-after`). A slow provider makes the run slow, but it doesn't change the result.
