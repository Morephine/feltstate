"""feltstate.sources.llm — measure affect with an OpenAI-compatible endpoint.

This source asks a chat model to *measure* how the character feels in reaction
to the latest user input, and returns the result as an
:class:`~feltstate.state.AffectDelta`. It talks to any endpoint that speaks the
OpenAI ``POST {base_url}/chat/completions`` shape — a local server (Ollama,
llama.cpp, vLLM, LM Studio, ...) or a hosted one. No API key is required for
local endpoints.

*Ground truth, not self-report.* The point of this module is that measuring
affect is a **separate step from generating the reply**. The model invoked here
is a *judge*, not the character: it observes the conversation from the outside
and reports a reading. Whatever model later writes the character's reply never
gets to declare how it feels — it only reads this measured state back (see
:mod:`feltstate.render`). Using the same underlying model for both jobs is fine;
the discipline is in keeping the two *calls* distinct, each with its own prompt.

Two robustness rules, both so the affect loop never takes down the agent:

* **Never raise.** Any failure — the endpoint is down, the request times out,
  the body is not JSON, a field is the wrong type — collapses to a near-neutral
  delta with low ``confidence``. A bad reading should look like "no clear
  signal," not like a crash.
* **Stdlib only.** Uses :mod:`urllib`; no third-party HTTP client. Drop it into
  any environment without pulling in dependencies.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional, Sequence

from ..config import DEFAULT_LABELS
from ..state import AffectDelta, AffectState
from .base import AffectSource, latest_user_text

# Valid label vocabulary, looked up case-insensitively when sanitising output.
_VALID_LABELS = {lbl.lower(): lbl for lbl in DEFAULT_LABELS}

# The judge's standing instruction. It frames the call as *measurement* and
# guards against the two classic failure modes (see base.py): self-report (the
# model narrating itself) and mirroring (echoing the user's mood back as the
# character's own). Output is constrained to a single JSON object.
_SYSTEM_PROMPT = (
    "You MEASURE the affect of a character (persona below) reacting to the "
    "latest user input. Output ONLY JSON "
    '{valence,arousal,labels,confidence,monologue}. '
    "The character reacts from its own baseline; do NOT mirror/paraphrase the "
    "user.\n"
    "Field meanings:\n"
    "- valence: float in [-1,1], how pleasant the character feels (negative = "
    "bad, positive = good).\n"
    "- arousal: float in [0,1], how activated/energised the character feels "
    "(0 = calm, 1 = highly activated).\n"
    "- labels: 0 to 3 discrete emotion words chosen ONLY from this list: "
    + ", ".join(DEFAULT_LABELS)
    + ".\n"
    "- confidence: float in [0,1], how clear the reading is (use a low value "
    "when the input is ambiguous or off-topic).\n"
    "- monologue: one short first-person sentence in the character's voice, or "
    '"" if nothing stands out.\n'
    "Return the JSON object and nothing else — no prose, no code fences."
)


class LLMSource(AffectSource):
    """Measure affect via an OpenAI-compatible ``/chat/completions`` endpoint.

    Parameters
    ----------
    base_url
        Endpoint root, e.g. ``"http://localhost:11434/v1"`` or
        ``"https://api.openai.com/v1"``. A trailing slash is fine; the path
        ``/chat/completions`` is appended.
    model
        Model name to request, e.g. ``"gpt-4o-mini"`` or a local model tag.
    api_key
        Bearer token. Optional — leave ``None`` for local endpoints that don't
        authenticate.
    timeout
        Per-request timeout in seconds. On timeout the source returns a neutral
        low-confidence delta rather than blocking the agent.

    Notes
    -----
    This source is deliberately *stateless* across turns: each :meth:`read`
    sends a fresh, self-contained request. State integration (smoothing,
    decay, pressure) is the engine's job, not the judge's.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        timeout: float = 20,
    ) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # AffectSource interface                                             #
    # ------------------------------------------------------------------ #
    def read(
        self,
        messages: Sequence[dict],
        *,
        baseline: AffectState,
        persona: str = "",
    ) -> AffectDelta:
        """Measure the character's reaction to the latest user message.

        Builds a measurement prompt from the persona, a short numeric summary
        of the standing baseline, and the recent conversation, then asks the
        endpoint for a JSON :class:`AffectDelta`. Any error path returns a
        near-neutral, low-confidence delta — this method never raises.
        """
        user_text = latest_user_text(messages)
        if not user_text.strip():
            # Nothing was said to the character this turn — no signal to read.
            return _neutral_delta()

        chat = [
            {"role": "system", "content": self._system_prompt(persona, baseline)},
            {"role": "user", "content": self._user_prompt(messages, user_text)},
        ]

        try:
            raw = self._post(chat)
            content = self._extract_content(raw)
            parsed = _parse_json_object(content)
        except Exception:
            # Network error, timeout, non-JSON body, malformed envelope —
            # treat any failure as "no clear signal", never as a crash.
            return _neutral_delta()

        if not isinstance(parsed, dict):
            return _neutral_delta()
        return _delta_from_measurement(parsed)

    # ------------------------------------------------------------------ #
    # Prompt construction                                                #
    # ------------------------------------------------------------------ #
    def _system_prompt(self, persona: str, baseline: AffectState) -> str:
        """System message: measurement instruction + persona + baseline summary."""
        parts = [_SYSTEM_PROMPT]
        persona = (persona or "").strip()
        if persona:
            parts.append("\n--- character persona ---\n" + persona)
        parts.append(
            "\n--- character's current standing baseline ---\n"
            + _baseline_summary(baseline)
            + "\nGround the reading in this baseline: the same words land "
            "differently on a wary character than on a trusting one."
        )
        return "\n".join(parts)

    def _user_prompt(self, messages: Sequence[dict], user_text: str) -> str:
        """User message: recent transcript plus the line to be appraised."""
        transcript = _format_transcript(messages)
        out = []
        if transcript:
            out.append("Recent conversation (oldest first):\n" + transcript)
        out.append(
            "\nMeasure how the character feels in reaction to the latest user "
            "message:\n" + user_text.strip()
        )
        return "\n".join(out)

    # ------------------------------------------------------------------ #
    # HTTP (stdlib urllib)                                               #
    # ------------------------------------------------------------------ #
    def _post(self, chat: list[dict]) -> dict:
        """POST the chat-completions request and return the decoded JSON body.

        Requests deterministic, JSON-only output. Raises on any transport or
        decode error; callers in :meth:`read` map that to a neutral delta.
        """
        body = {
            "model": self.model,
            "messages": chat,
            "temperature": 0.0,           # measurement should be repeatable
            "max_tokens": 256,
            # Honoured by OpenAI and most compatible servers; harmlessly
            # ignored by those that don't implement it.
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key

        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = resp.read().decode("utf-8", "replace")
        return json.loads(payload)

    @staticmethod
    def _extract_content(raw: dict) -> str:
        """Pull the assistant message text out of an OpenAI-shaped response."""
        choices = raw.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")


# --------------------------------------------------------------------------- #
# Baseline / transcript summaries (compact, model-readable)                   #
# --------------------------------------------------------------------------- #
def _baseline_summary(baseline: AffectState) -> str:
    """A few numbers describing the standing state, for the judge to ground in.

    Deliberately terse: traits, current mood, and relationship as 0..1 figures.
    The judge needs the *shape* of the character, not the full state object.
    """
    t = baseline.traits
    m = baseline.mood
    r = baseline.relationship
    return (
        "traits: "
        f"depression={t.depression:.2f}, optimism={t.optimism:.2f}, "
        f"anxiety={t.anxiety:.2f}, curiosity={t.curiosity:.2f}\n"
        "mood: "
        f"valence={m.valence:.2f}, arousal={m.arousal:.2f}\n"
        "relationship: "
        f"closeness={r.closeness:.2f}, trust={r.trust:.2f}, "
        f"safety={r.safety:.2f}, unresolved_tension={r.unresolved_tension:.2f}"
    )


def _format_transcript(messages: Sequence[dict], max_turns: int = 8) -> str:
    """Render the last few turns as ``role: content`` lines.

    Trimmed to the most recent ``max_turns`` so the prompt stays small. The
    final user line is appraised explicitly in the user prompt, so a little
    overlap with the transcript is fine — it gives the judge context.
    """
    lines = []
    for m in list(messages)[-max_turns:]:
        role = (m.get("role") or "").strip() or "user"
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        # Keep individual turns from blowing up the prompt.
        if len(content) > 500:
            content = content[:500] + " ..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Parsing & sanitising the measurement                                        #
# --------------------------------------------------------------------------- #
def _parse_json_object(text: str) -> Optional[dict]:
    """Best-effort extraction of a single JSON object from model output.

    Tries a direct parse first; if the model wrapped the object in prose or a
    code fence, falls back to the substring between the first ``{`` and the
    last ``}``. Returns ``None`` if nothing parses.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _coerce_float(value, default: float) -> float:
    """Tolerantly turn a measurement field into a float, or fall back."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _clean_labels(value) -> list[str]:
    """Keep only known labels (case-insensitive), de-duped, capped at 3.

    Anything the judge invents outside :data:`DEFAULT_LABELS` is dropped — the
    rest of the system routes labels through fixed maps, so an unknown label is
    just noise. Accepts a list or a comma-separated string.
    """
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return []

    out: list[str] = []
    for item in items:
        canonical = _VALID_LABELS.get(str(item).strip().lower())
        if canonical and canonical not in out:
            out.append(canonical)
        if len(out) >= 3:
            break
    return out


def _delta_from_measurement(d: dict) -> AffectDelta:
    """Turn a parsed measurement dict into a schema-clamped :class:`AffectDelta`.

    Coerces and range-clamps every field so a sloppy or partial response still
    yields a valid delta. Unknown labels are discarded; missing fields take
    neutral defaults.
    """
    valence = _clamp(_coerce_float(d.get("valence"), 0.0), -1.0, 1.0)
    arousal = _clamp(_coerce_float(d.get("arousal"), 0.4), 0.0, 1.0)
    confidence = _clamp(_coerce_float(d.get("confidence"), 0.5), 0.0, 1.0)
    labels = _clean_labels(d.get("labels"))
    monologue = str(d.get("monologue") or "").strip()

    return AffectDelta(
        valence=valence,
        arousal=arousal,
        labels=labels,
        confidence=confidence,
        monologue=monologue,
    )


def _neutral_delta() -> AffectDelta:
    """A near-neutral reading used whenever the signal is unclear or a call
    fails. Low ``confidence`` tells the engine to weight it lightly, so a string
    of failures decays the agent toward neutral instead of corrupting state."""
    return AffectDelta(valence=0.0, arousal=0.4, labels=[], confidence=0.1, monologue="")
