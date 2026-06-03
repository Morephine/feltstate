"""feltstate.memory.extract — propose 5W1H facts from conversation, optionally.

Memory is a tool, not a controller: ultimately the agent (or the application)
decides what is worth keeping. But scanning every turn by hand is tedious, so
this module offers an *optional* helper: a **second model pass**, separate from
the one that generates the agent's replies, that reads a slice of conversation
and proposes structured 5W1H facts. You then choose what to commit to a
:class:`~feltstate.memory.canon.Canon`.

This mirrors the affect side of feltstate. Just as affect is *measured* by a
separate :class:`~feltstate.sources.base.AffectSource` rather than self-reported
by the reply model, facts are *extracted* by a separate pass rather than the
reply model deciding mid-sentence what to remember. Keeping the two jobs in
distinct calls is the whole discipline.

feltstate does not care which model does the extraction — point
:class:`LLMFactExtractor` at any OpenAI-compatible endpoint, or implement
:class:`FactExtractor` yourself (a small local model, a classifier, a rules
pass). Each proposed fact is a plain dict ready for :meth:`Canon.add` /
:meth:`Canon.ask`; by default :func:`commit_to_canon` files them in the grey
zone so the agent confirms what to keep rather than having memory written behind
its back.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Sequence

from .canon import Canon


class FactExtractor(ABC):
    """Proposes 5W1H facts from a slice of conversation."""

    @abstractmethod
    def extract(self, messages: Sequence[dict], *, actor_hint: str = "user") -> list[dict]:
        """Return proposed facts as plain dicts, each shaped for :meth:`Canon.add`.

        Parameters
        ----------
        messages
            Recent conversation as ``[{"role", "content"}, ...]``.
        actor_hint
            Who facts default to when the speaker is unclear (e.g. the user's
            name or ``"user"``).

        Returns
        -------
        list[dict]
            Each item: ``{"actor", "object", "why", "when"?, "intensity"?}``.
            ``object`` is *what* is true (one clause); ``why`` is what it means /
            the feeling behind it — an object says what happened, a why says why
            it is worth keeping. Empty list when nothing durable was said. An
            implementation must never raise; return ``[]`` on any failure.
        """
        raise NotImplementedError


_SYSTEM_PROMPT = (
    "You read a slice of conversation and extract durable FACTS worth remembering "
    "long-term: stable preferences, decisions, commitments, important events, and "
    "things a speaker clearly cares about. Ignore small talk and anything "
    "ephemeral.\n"
    "Output ONLY a JSON array. Each item is an object with:\n"
    '- "actor": who the fact is about (a name, or the given default).\n'
    '- "object": what is true, as one short clause.\n'
    '- "why": why it matters — the meaning or feeling behind it.\n'
    '- "intensity": 0..1, how important/durable (use 0.5 if unsure; >0.85 only for '
    "core, permanent things).\n"
    "Return [] if nothing is worth keeping. No prose, no code fences — just the JSON array."
)


class LLMFactExtractor(FactExtractor):
    """Extract facts with a second LLM call against an OpenAI-compatible endpoint.

    Talks to any ``POST {base_url}/chat/completions`` server (local or hosted).
    Using a smaller/cheaper model than the reply model for this job is common and
    sensible, but not required — feltstate is model-agnostic here.

    Parameters
    ----------
    base_url, model, api_key, timeout
        As for :class:`~feltstate.sources.llm.LLMSource`.
    max_facts
        Cap on how many facts to return from one pass.

    Robustness: like the affect sources, this **never raises**. Any failure
    (endpoint down, timeout, non-JSON body) yields an empty list — a missed
    extraction should look like "nothing to record this turn", not a crash.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 20,
        max_facts: int = 8,
    ) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_facts = int(max_facts)

    def extract(self, messages: Sequence[dict], *, actor_hint: str = "user") -> list[dict]:
        transcript = _format_transcript(messages)
        if not transcript.strip():
            return []
        chat = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Default actor when the speaker is unclear: {actor_hint}\n\n"
                    f"Conversation:\n{transcript}"
                ),
            },
        ]
        try:
            raw = self._post(chat)
            content = _extract_content(raw)
            facts = _parse_fact_array(content)
        except Exception:
            return []
        return _clean_facts(facts, actor_hint, self.max_facts)

    def _post(self, chat: list[dict]) -> dict:
        body = {
            "model": self.model,
            "messages": chat,
            "temperature": 0.0,
            "max_tokens": 512,
        }
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = resp.read().decode("utf-8", "replace")
        return json.loads(payload)


def commit_to_canon(
    facts: list[dict],
    canon: Canon,
    *,
    grey_zone: bool = True,
    default_intensity: float = 0.5,
) -> list[dict]:
    """Write proposed ``facts`` into a :class:`Canon` and return the stored entries.

    By default facts land in the **grey zone** (:meth:`Canon.ask`) rather than the
    confirmed store: an extraction is a *suggestion*, and the agent should get to
    confirm what it actually keeps (memory is its tool, not something written
    behind its back). Pass ``grey_zone=False`` to add directly to the confirmed
    store when you trust the extractor.

    Facts missing an ``object`` are skipped.
    """
    stored: list[dict] = []
    for f in facts or []:
        obj = str(f.get("object", "") or "").strip()
        if not obj:
            continue
        actor = str(f.get("actor", "") or "user").strip() or "user"
        why = str(f.get("why", "") or "")
        when = str(f.get("when", "") or "")
        intensity = f.get("intensity")
        intensity = float(intensity) if isinstance(intensity, (int, float)) else default_intensity
        write = canon.ask if grey_zone else canon.add
        stored.append(write(actor, obj, why=why, when=when, intensity=intensity))
    return stored


# --------------------------------------------------------------------------- #
# Helpers (parse / sanitise) — shared shape with sources.llm                  #
# --------------------------------------------------------------------------- #
def _format_transcript(messages: Sequence[dict], max_turns: int = 20) -> str:
    lines = []
    for m in list(messages)[-max_turns:]:
        role = (m.get("role") or "").strip() or "user"
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 800:
            content = content[:800] + " ..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _extract_content(raw: dict) -> str:
    choices = raw.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def _parse_fact_array(text: str) -> list:
    """Best-effort extraction of a JSON array from model output.

    Direct parse first; if wrapped in prose/fences, fall back to the substring
    between the first ``[`` and the last ``]``. Returns ``[]`` on failure.
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else []
    except (json.JSONDecodeError, ValueError):
        pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _clean_facts(facts: list, actor_hint: str, max_facts: int) -> list[dict]:
    """Keep well-formed fact dicts, fill defaults, clamp intensity, cap the count."""
    out: list[dict] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        obj = str(f.get("object", "") or "").strip()
        if not obj:
            continue
        intensity = f.get("intensity")
        intensity = float(intensity) if isinstance(intensity, (int, float)) else 0.5
        out.append(
            {
                "actor": str(f.get("actor", "") or actor_hint).strip() or actor_hint,
                "object": obj,
                "why": str(f.get("why", "") or "").strip(),
                "when": str(f.get("when", "") or "").strip(),
                "intensity": max(0.0, min(1.0, intensity)),
            }
        )
        if len(out) >= max_facts:
            break
    return out
