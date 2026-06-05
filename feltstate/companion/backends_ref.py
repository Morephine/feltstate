"""feltstate.companion.backends_ref — a reference LLMBackend over OpenAI-compatible HTTP.

Optional and still zero-dependency: stdlib :mod:`urllib`, the same transport
:class:`~feltstate.sources.llm.LLMSource` uses. Points at any endpoint speaking
the OpenAI ``POST {base_url}/chat/completions`` shape (Ollama, llama.cpp, vLLM,
LM Studio, a hosted provider). Never raises on a transient failure — returns
``""`` so the companion loop survives a down endpoint.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .backend import LLMBackend


class OpenAICompatBackend(LLMBackend):
    """Reply backend for any OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434/v1",
        model: str = "llama3.1",
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 300,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, messages: list[dict]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            # Many OpenAI-compatible servers cache the longest static prefix; this
            # generic hint is harmlessly ignored by servers that don't know it.
            "cache_prompt": True,
        }
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read().decode("utf-8", "replace")
            raw = json.loads(payload)
        except (urllib.error.URLError, OSError, ValueError):
            # The reply model is the agent's voice; a down endpoint must fail
            # soft (empty reply), never crash the loop.
            return ""
        choices = raw.get("choices") or []
        if not choices:
            return ""
        return str((choices[0].get("message") or {}).get("content") or "")
