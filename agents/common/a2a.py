"""Minimal A2A v0.3 (JSON-RPC 2.0 over HTTP) server and client, using only the standard library.

Only what this exercise needs is implemented:
  * GET  /.well-known/agent-card.json  -> the agent card
  * POST <card.url>                    -> JSON-RPC method "message/send"

An agent supplies a handler: handler(message: dict, task: dict | None) -> dict (a Task or a Message).
The server keeps tasks in memory so a second message with the same taskId continues the conversation.
"""
from __future__ import annotations

import json
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

PROTOCOL_VERSION = "0.3.0"
# Agents talk to each other directly; never route local A2A traffic through an HTTP proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CARD_PATH = "/.well-known/agent-card.json"
RPC_PATH = "/a2a"

# JSON-RPC / A2A error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TASK_NOT_FOUND = -32001


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4()}"


# ---------------------------------------------------------------- message / task builders

def text_part(text: str) -> dict:
    return {"kind": "text", "text": text}


def data_part(data: dict) -> dict:
    return {"kind": "data", "data": data}


def make_message(parts: list, role: str = "user", task_id: str | None = None,
                 context_id: str | None = None) -> dict:
    msg = {"kind": "message", "role": role, "messageId": new_id(), "parts": parts}
    if task_id:
        msg["taskId"] = task_id
    if context_id:
        msg["contextId"] = context_id
    return msg


def make_task(context_id: str | None = None, task_id: str | None = None) -> dict:
    return {
        "kind": "task",
        "id": task_id or new_id("task-"),
        "contextId": context_id or new_id("ctx-"),
        "status": {"state": "submitted", "timestamp": now_iso()},
        "artifacts": [],
        "history": [],
    }


def set_status(task: dict, state: str, text: str | None = None, data: dict | None = None) -> dict:
    status = {"state": state, "timestamp": now_iso()}
    if text is not None or data is not None:
        parts = []
        if text is not None:
            parts.append(text_part(text))
        if data is not None:
            parts.append(data_part(data))
        status["message"] = make_message(parts, role="agent", task_id=task["id"],
                                         context_id=task["contextId"])
    task["status"] = status
    return task


def first_data(message_or_parts) -> Optional[dict]:
    parts = message_or_parts.get("parts", []) if isinstance(message_or_parts, dict) else message_or_parts
    for p in parts or []:
        if p.get("kind") == "data":
            return p.get("data")
    return None


def first_text(message_or_parts) -> Optional[str]:
    parts = message_or_parts.get("parts", []) if isinstance(message_or_parts, dict) else message_or_parts
    for p in parts or []:
        if p.get("kind") == "text":
            return p.get("text")
    return None


# ---------------------------------------------------------------- server

class A2AServer:
    """Runs one agent on a background thread. Call start() and stop()."""

    def __init__(self, card: dict, handler: Callable[[dict, Optional[dict]], dict],
                 host: str = "127.0.0.1", port: int = 0):
        self.card = card
        self.handler = handler
        self.tasks: dict[str, dict] = {}
        self.lock = threading.Lock()
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep stdout clean
                pass

            def _send(self, code: int, body: dict):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == CARD_PATH:
                    self._send(200, server.card)
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                if self.path != RPC_PATH:
                    self._send(404, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length", 0))
                try:
                    req = json.loads(self.rfile.read(length))
                except json.JSONDecodeError:
                    self._send(200, rpc_error(None, PARSE_ERROR, "Parse error"))
                    return
                self._send(200, server.dispatch(req))

        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://{host}:{self.port}"
        self.card["url"] = self.base_url + RPC_PATH
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "A2AServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def dispatch(self, req: dict) -> dict:
        rid = req.get("id")
        if req.get("jsonrpc") != "2.0" or "method" not in req:
            return rpc_error(rid, INVALID_REQUEST, "Invalid Request")
        if req["method"] != "message/send":
            return rpc_error(rid, METHOD_NOT_FOUND, f"Method not found: {req['method']}")
        message = (req.get("params") or {}).get("message")
        if not isinstance(message, dict) or not message.get("parts"):
            return rpc_error(rid, INVALID_PARAMS, "params.message with parts is required")

        task = None
        task_id = message.get("taskId")
        if task_id:
            with self.lock:
                task = self.tasks.get(task_id)
            if task is None:
                return rpc_error(rid, TASK_NOT_FOUND, f"Task not found: {task_id}")
            if task["status"]["state"] in ("completed", "failed", "canceled", "rejected"):
                return rpc_error(rid, INVALID_PARAMS, f"Task {task_id} is already {task['status']['state']}")
            if message.get("contextId") and message["contextId"] != task["contextId"]:
                return rpc_error(rid, INVALID_PARAMS, "contextId does not match the task")
        try:
            result = self.handler(message, task)
        except Exception as exc:  # report, don't crash the server
            return rpc_error(rid, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        if result.get("kind") == "task":
            result.setdefault("history", []).append(message)
            with self.lock:
                self.tasks[result["id"]] = result
        return {"jsonrpc": "2.0", "id": rid, "result": result}


def rpc_error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------- client

class A2AClient:
    """Sends message/send to another agent and logs every request and response."""

    def __init__(self, base_url: str, sender: str, receiver: str, log_path: str | None = None,
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.sender = sender
        self.receiver = receiver
        self.log_path = log_path
        self.timeout = timeout
        self._card = None

    def card(self) -> dict:
        if self._card is None:
            with _OPENER.open(self.base_url + CARD_PATH, timeout=self.timeout) as r:
                self._card = json.loads(r.read())
        return self._card

    def send(self, message: dict) -> dict:
        """Send a message; return the JSON-RPC result (Task or Message). Raises on JSON-RPC error."""
        req = {"jsonrpc": "2.0", "id": new_id(), "method": "message/send", "params": {"message": message}}
        self._log("request", req)
        http_req = urllib.request.Request(self.card()["url"], data=json.dumps(req).encode(),
                                          headers={"Content-Type": "application/json"}, method="POST")
        with _OPENER.open(http_req, timeout=self.timeout) as r:
            resp = json.loads(r.read())
        self._log("response", resp)
        if "error" in resp:
            raise RuntimeError(f"A2A error from {self.receiver}: {resp['error']}")
        return resp["result"]

    def _log(self, direction: str, payload: dict) -> None:
        if not self.log_path:
            return
        frm, to = (self.sender, self.receiver) if direction == "request" else (self.receiver, self.sender)
        line = {"timestamp": now_iso(), "direction": direction, "from": frm, "to": to, "payload": payload}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
