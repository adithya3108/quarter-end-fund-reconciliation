"""Tiny client for any OpenAI-compatible /chat/completions endpoint.

Reads LLM_API_KEY, LLM_BASE_URL and LLM_MODEL from the environment. No provider is hard-coded.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout: float = 120.0, max_retries: int = 5):
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL", "")
        self.timeout = timeout
        self.max_retries = max_retries
        missing = [n for n, v in (("LLM_API_KEY", self.api_key), ("LLM_BASE_URL", self.base_url),
                                  ("LLM_MODEL", self.model)) if not v]
        if missing:
            raise LLMError(f"Missing environment variables: {', '.join(missing)}")

    def chat(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}",
                     # urllib's default UA is blocked (403, code 1010) by some providers' Cloudflare rules
                     "User-Agent": "ritamm-gpf-close/1.0", "Accept": "application/json"})
        delay = 2.0
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read())
                return payload["choices"][0]["message"]["content"] or ""
            except urllib.error.HTTPError as e:
                # 429 (rate limit) and 5xx are worth retrying; free tiers hit 429 often.
                if e.code == 429 or e.code >= 500:
                    wait = _retry_after(e) or delay
                    if attempt < self.max_retries:
                        time.sleep(min(wait, 60))
                        delay *= 2
                        continue
                detail = e.read().decode(errors="replace")[:500]
                raise LLMError(f"LLM HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(f"LLM unreachable: {e}") from e
        raise LLMError("LLM retries exhausted")

    def chat_json(self, system: str, user: str):
        return extract_json(self.chat(system, user))


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    value = e.headers.get("retry-after") if e.headers else None
    try:
        return float(value) if value else None
    except ValueError:
        return None


def extract_json(text: str):
    """Parse JSON from a model reply, tolerating ```json fences or leading prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No JSON found in LLM reply: {text[:200]!r}")
