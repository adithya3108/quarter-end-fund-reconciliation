"""The A2A conversation over real local HTTP (no LLM, no external network)."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from agents.common import a2a
from agents.valuation_agent.agent import ValuationAgent, agent_card


@pytest.fixture()
def agent_b(q3, agent_b_data, tmp_path):
    agent = ValuationAgent(agent_b_data, q3, Decimal("1.0666"), Decimal("15"))
    server = a2a.A2AServer(agent_card(), agent.handle, port=0).start()
    client = a2a.A2AClient(server.base_url, "agent_a", "agent_b", str(tmp_path / "log.jsonl"))
    yield client, tmp_path / "log.jsonl"
    server.stop()


GOOD = {"purchases": [{"company": "IONMERE MATERIALS LTD", "date": "2022-12-15", "amount_usd": 14000000.0}],
        "sales": [{"company": "Fjord Payments AS", "date": "2022-11-18", "proceeds_usd": 29000000.0}]}


def _turn1(client):
    return client.send(a2a.make_message([a2a.data_part({"fund": "GPF", "valuation_date": "2022-12-31"})]))


def _turn2(client, task, data):
    return client.send(a2a.make_message([a2a.data_part(data)], task_id=task["id"], context_id=task["contextId"]))


def test_agent_card_is_served(agent_b):
    client, _ = agent_b
    card = client.card()
    assert card["protocolVersion"] == "0.3.0" and card["url"].endswith("/a2a")


def test_two_turn_conversation_releases_marks(agent_b):
    client, log = agent_b
    task = _turn1(client)
    assert task["status"]["state"] == "input-required"
    assert "purchases and sales" in a2a.first_text(task["status"]["message"])
    done = _turn2(client, task, GOOD)
    assert done["status"]["state"] == "completed" and done["id"] == task["id"]
    data = done["artifacts"][0]["parts"][0]["data"]
    assert done["artifacts"][0]["artifactId"] == "marks-GPF-2022-12-31"
    assert len(data["marks"]) == 7 and data["marks"][0]["fair_value"] == 25700000.0
    lines = [json.loads(l) for l in Path(log).read_text().splitlines()]
    assert [l["direction"] for l in lines] == ["request", "response"] * 2
    assert all("timestamp" in l for l in lines)


def test_wrong_lists_stay_input_required_then_can_be_fixed(agent_b):
    client, _ = agent_b
    task = _turn1(client)
    bad = {"purchases": GOOD["purchases"], "sales": [{**GOOD["sales"][0], "proceeds_usd": 2900000.0}]}
    again = _turn2(client, task, bad)
    assert again["status"]["state"] == "input-required"
    assert "1 of 2 items matched" in a2a.first_text(again["status"]["message"])
    assert _turn2(client, again, GOOD)["status"]["state"] == "completed"


def test_empty_or_extra_lists_do_not_match(agent_b):
    client, _ = agent_b
    task = _turn1(client)
    assert _turn2(client, task, {"purchases": [], "sales": []})["status"]["state"] == "input-required"
    extra = {**GOOD, "purchases": GOOD["purchases"] * 2}
    assert _turn2(client, task, extra)["status"]["state"] == "input-required"


def test_unknown_task_and_method_are_json_rpc_errors(agent_b):
    client, _ = agent_b
    with pytest.raises(RuntimeError, match="Task not found"):
        client.send(a2a.make_message([a2a.data_part(GOOD)], task_id="task-nope", context_id="x"))
    import urllib.request
    req = urllib.request.Request(client.card()["url"], method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tasks/get"}).encode())
    resp = json.loads(a2a._OPENER.open(req).read())
    assert resp["error"]["code"] == a2a.METHOD_NOT_FOUND


def test_end_to_end_main_with_fake_llm(pack, tmp_path, monkeypatch):
    import main
    from tests.fakes import RuleBasedLLM
    monkeypatch.setenv("AGENT_A_PORT", "0")
    monkeypatch.setenv("AGENT_B_PORT", "0")
    rc = main.run(pack, tmp_path, Decimal("15"), llm=RuleBasedLLM())
    assert rc == 0
    nav = json.loads((tmp_path / "nav.json").read_text())
    assert nav["nav_q4"] == 169764039.25 and nav["bridge"]["difference"] == 0.0
    assert len(json.loads((tmp_path / "transactions.json").read_text())) == 39
    assert len((tmp_path / "a2a_log.jsonl").read_text().splitlines()) == 4


def test_main_accepts_brief_style_positional_base(monkeypatch, tmp_path):
    """`python main.py ./pack .` (the brief's example) must parse; output goes to <base>/output."""
    import main
    seen = {}
    monkeypatch.setattr(main, "run", lambda pack, out, thr, llm=None: seen.update(pack=pack, out=out) or 0)
    monkeypatch.setattr(main, "load_dotenv", lambda p: None)
    assert main.main(["./pack", str(tmp_path)]) == 0
    assert seen["out"] == tmp_path / "output" and str(seen["pack"]) == "pack"
