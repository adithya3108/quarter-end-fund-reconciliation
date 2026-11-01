"""The OpenAI-compatible client against a local fake endpoint (no real provider)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agents.common.llm import LLMClient, LLMError, extract_json


@pytest.fixture()
def fake_endpoint(monkeypatch):
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(v, raising=False)
    state = {"calls": 0, "seen": None}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            state["calls"] += 1
            state["seen"] = (self.path, self.headers.get("Authorization"),
                             json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if state["calls"] == 1:  # first call is rate-limited
                self.send_response(429)
                self.send_header("retry-after", "0")
                self.end_headers()
                return
            body = json.dumps({"choices": [{"message": {"content": '```json\n[{"i": 0}]\n```'}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", state
    srv.shutdown()


def test_reads_env_retries_429_and_parses_fenced_json(fake_endpoint, monkeypatch):
    url, state = fake_endpoint
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", url)
    monkeypatch.setenv("LLM_MODEL", "some-model")
    assert LLMClient().chat_json("s", "u") == [{"i": 0}]
    path, auth, body = state["seen"]
    assert path == "/v1/chat/completions" and auth == "Bearer test-key" and body["model"] == "some-model"
    assert state["calls"] == 2


def test_missing_env_is_a_clear_error(monkeypatch):
    for v in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        LLMClient()


def test_extract_json_tolerates_prose():
    assert extract_json('Here you go: [{"i": 1}] hope that helps') == [{"i": 1}]
