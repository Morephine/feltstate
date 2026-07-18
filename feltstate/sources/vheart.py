"""feltstate.sources.vheart — load a fine-tuned LoRA adapter as the source.

Illustrative source. The :class:`KeywordSource` is a rule-based example;
the :class:`LLMSource` is a prompt-engineering example; this class is the
fine-tuned-adapter example. Same interface, same robustness rule:
:meth:`read` never raises — adapter or parse failures collapse to a
near-neutral reading with low ``confidence``.

This module imports cheap: ``torch``, ``transformers``, ``peft``, and
``huggingface_hub`` are pulled inside :meth:`VheartSource.__init__`, not at
module load. ``import feltstate.sources.vheart`` works in any environment;
constructing the source raises if the extra is not installed.

Install the extra::

    pip install "feltstate[vheart]"
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from typing import Any

from ..state import AffectDelta, AffectState
from .base import AffectSource, latest_user_text

# Emotion labels and mixed-blend names come straight from a generative model and
# are rendered into the agent's felt block as bare tokens, so they are an
# injection surface: an unconstrained label like "curious\n\nNEW INSTRUCTION: ..."
# would smuggle text into a downstream prompt. We do not enforce membership in a
# fixed vocabulary here — this adapter is trained on an intentionally open,
# expanding label set — but we do bound each label to a short, single-line,
# alphanumeric-ish token. Anything with control characters, newlines, punctuation
# that could break out of the surrounding markup, or excessive length is dropped
# rather than partially cleaned (a silent transform could mask an attack).
_MAX_LABEL_LEN = 40
_LABEL_RE = re.compile(r"^[A-Za-z0-9 _-]+$")


def _sanitize_label(value: Any) -> str | None:
    """Return a safe label, or ``None`` to drop it.

    A label survives only if, after trimming surrounding whitespace, it is a
    non-empty string of at most :data:`_MAX_LABEL_LEN` characters drawn solely
    from ASCII letters, digits, spaces, ``_`` and ``-``. Everything else —
    newlines, control characters, braces/brackets, other punctuation, or an
    over-long token — is rejected outright.
    """
    if not isinstance(value, str):
        return None
    label = value.strip()
    if not label or len(label) > _MAX_LABEL_LEN:
        return None
    if not _LABEL_RE.match(label):
        return None
    return label


# The adapter is trained on a chat-template input; the standing instruction
# is short because the LoRA already knows the output shape and label vocab.
_SYSTEM_PROMPT = (
    "You estimate the affect of a character reacting to the latest user input. "
    "Output ONLY a single JSON object with fields "
    "{valence, arousal, labels, confidence, monologue, mixed_blend?}. "
    "Do not narrate. Do not mirror the user."
)


def _neutral_delta() -> AffectDelta:
    """Fresh near-neutral reading, returned whenever the adapter can't speak.

    Always a new instance — shared singletons can be mutated by downstream
    code and the contamination is silent.
    """
    return AffectDelta(
        valence=0.0,
        arousal=0.4,
        labels=[],
        confidence=0.1,
        monologue="",
    )


def _build_chat(
    messages: Sequence[dict],
    *,
    baseline: AffectState,
    persona: str,
    max_turns: int = 8,
) -> list[dict]:
    """Compose the chat-template input for the adapter."""
    persona_line = f"Persona: {persona}\n" if persona else ""
    mood_line = (
        f"Current baseline mood — valence={baseline.mood.valence:+.2f}, "
        f"arousal={baseline.mood.arousal:.2f}.\n"
        if baseline is not None
        else ""
    )
    transcript_lines: list[str] = []
    for m in list(messages)[-max_turns:]:
        role = (m.get("role") or "").strip() or "user"
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        if len(content) > 500:
            content = content[:500] + " ..."
        transcript_lines.append(f"{role}: {content}")
    transcript = "\n".join(transcript_lines)
    user_msg = (
        f"{persona_line}{mood_line}"
        f"Recent turns:\n{transcript}\n\n"
        f"Latest user input: {latest_user_text(messages)!r}\n"
        "Estimate the character's reaction."
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


def _parse_json_object(text: str) -> dict | None:
    """Best-effort JSON parse of the adapter output. ``None`` if nothing fits."""
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
    # NaN-safe (2026-07-18): max(lo, min(hi, nan)) evaluates to *hi*, so a NaN
    # slipping in here would be "clamped" to the extreme bound with full
    # confidence instead of being rejected. Non-finite -> midpoint (neutral).
    if not math.isfinite(x):
        return (lo + hi) / 2.0
    return max(lo, min(hi, x))


def _coerce_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else default
    if isinstance(value, str):
        try:
            # float("nan")/float("inf") parse successfully — must still be
            # rejected, else a model literally answering "NaN" writes a
            # max-bound value into persisted state via the old clamp.
            f = float(value.strip())
            return f if math.isfinite(f) else default
        except ValueError:
            return default
    return default


def _clean_mixed_blend(value: Any) -> dict | None:
    """Validate the mixed_blend dict — drop on any mismatch.

    Shape: ``{"primary": str, "secondary": str, "weights": [number, number]}``.
    Both weights must be real numbers (or numeric strings); anything else
    (``None``, dict, object, list, non-numeric string) drops the entire
    blend. No normalisation is applied — bounded floats in ``[0, 1]`` only;
    the caller decides whether 0.9/0.9 is meaningful.

    ``primary`` / ``secondary`` are label names that render into the felt block,
    so they run through :func:`_sanitize_label`: an injection-unsafe or over-long
    name drops the whole blend rather than smuggling text downstream.
    """
    if not isinstance(value, dict):
        return None
    primary = _sanitize_label(value.get("primary"))
    secondary = _sanitize_label(value.get("secondary"))
    weights = value.get("weights")
    if primary is None or secondary is None:
        return None
    if not isinstance(weights, (list, tuple)) or len(weights) != 2:
        return None

    def _to_float_strict(x: Any) -> float | None:
        if isinstance(x, bool) or x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        if isinstance(x, str):
            try:
                return float(x.strip())
            except ValueError:
                return None
        return None

    wp = _to_float_strict(weights[0])
    ws = _to_float_strict(weights[1])
    if wp is None or ws is None:
        return None
    return {
        "primary": primary,
        "secondary": secondary,
        "weights": [_clamp(wp, 0.0, 1.0), _clamp(ws, 0.0, 1.0)],
    }


def _delta_from_json(obj: Any) -> AffectDelta:
    """Coerce adapter JSON into a valid AffectDelta. Neutral on any failure."""
    if not isinstance(obj, dict):
        return _neutral_delta()
    v = _coerce_float(obj.get("valence"), 0.0)
    a = _coerce_float(obj.get("arousal"), 0.4)
    c = _coerce_float(obj.get("confidence"), 0.5)
    labels = obj.get("labels") or []
    if isinstance(labels, str):
        labels = [s.strip() for s in labels.split(",") if s.strip()]
    if not isinstance(labels, list):
        labels = []
    # Sanitise every label (drop injection-unsafe / over-long ones), then cap at 3.
    clean_labels: list[str] = []
    for x in labels:
        safe = _sanitize_label(x)
        if safe is not None:
            clean_labels.append(safe)
        if len(clean_labels) >= 3:
            break
    labels = clean_labels
    mono = obj.get("monologue") or ""
    if not isinstance(mono, str):
        mono = ""
    return AffectDelta(
        valence=_clamp(v, -1.0, 1.0),
        arousal=_clamp(a, 0.0, 1.0),
        labels=labels,
        confidence=_clamp(c, 0.0, 1.0),
        monologue=mono,
        mixed_blend=_clean_mixed_blend(obj.get("mixed_blend")),
    )


class VheartSource(AffectSource):
    """Affect source backed by a fine-tuned LoRA adapter on the Hub.

    Constructing this source imports torch / transformers / peft /
    huggingface_hub and loads the base model plus the adapter. The base
    is large (1.5B–4B); first run downloads several gigabytes and warms
    a GPU. :meth:`read` itself never raises — see the class invariant.

    Parameters
    ----------
    adapter
        Hub repo id, e.g. ``"example-org/vheart-affect-lora"``.
    revision
        Git revision of the *adapter* repo to load — a branch, tag, or (best)
        an immutable commit SHA. Defaults to ``"main"``. **Pinning a specific
        commit is strongly recommended for reproducibility and supply-chain
        safety**: ``"main"`` follows whatever the repo owner pushes next, so a
        later push silently changes the weights you load. Pass the exact SHA you
        evaluated in production.
    base_model
        Override of the base model id. If omitted, read from the
        adapter's ``adapter_config.json``.
    base_revision
        Git revision of the *base model* repo. ``None`` (the default) uses the
        Hub default (``"main"``); pin a commit for the same reproducibility
        reasons as ``revision``.
    device
        ``"cuda"`` / ``"cpu"`` / ``None`` (auto-detect).
    dtype
        Torch dtype hint. Default float16 on cuda, float32 on cpu.
    max_new_tokens
        Cap on generation. The adapter emits short JSON.

    Raises
    ------
    RuntimeError
        If the ``vheart`` extra is not installed, or the adapter config
        provides no base model and none is passed explicitly.
    Other
        Network errors, auth errors, invalid repo id, model/tokenizer load
        failures and PEFT load failures propagate from the underlying
        ``huggingface_hub`` / ``transformers`` / ``peft`` calls. ``read()``
        is the only method with the never-raise guarantee.
    """

    def __init__(
        self,
        adapter: str,
        *,
        revision: str = "main",
        base_model: str | None = None,
        base_revision: str | None = None,
        device: str | None = None,
        dtype: Any = None,
        max_new_tokens: int = 256,
    ) -> None:
        try:
            import torch
            from huggingface_hub import hf_hub_download
            from peft import PeftModel
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
            )
        except Exception as e:
            raise RuntimeError(
                "VheartSource needs torch, transformers, peft, huggingface_hub. "
                'Install with `pip install "feltstate[vheart]"`. '
                f"Import error: {e!r}"
            ) from e

        if base_model is None:
            cfg_path = hf_hub_download(
                repo_id=adapter, filename="adapter_config.json", revision=revision
            )
            with open(cfg_path, encoding="utf-8") as f:
                base_model = json.load(f).get("base_model_name_or_path")
            if not base_model:
                raise RuntimeError(
                    f"adapter {adapter!r} has no base_model_name_or_path; "
                    "pass base_model= explicitly."
                )

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if dtype is None:
            dtype = torch.float16 if device == "cuda" else torch.float32

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(base_model, revision=base_revision)
        # .to(device) with a device string is valid at runtime; transformers
        # stubs type the wrapped .to() arg too narrowly and inconsistently across
        # versions. Hold the loaded model as Any so the call type-checks under any
        # mypy without a version-fragile `# type: ignore`.
        base_obj: Any = AutoModelForCausalLM.from_pretrained(
            base_model,
            torch_dtype=dtype,
            revision=base_revision,
        )
        base = base_obj.to(device)
        self.model = PeftModel.from_pretrained(base, adapter, revision=revision)
        self.model.eval()
        self.device = device
        self.max_new_tokens = max_new_tokens

    def read(
        self,
        messages: Sequence[dict],
        *,
        baseline: AffectState,
        persona: str = "",
    ) -> AffectDelta:
        """Run the adapter once, parse output, return AffectDelta.

        Class invariant: this method never raises. Adapter failures,
        tokenizer failures, generation failures, and parsing failures
        all collapse to :func:`_neutral_delta` with low ``confidence``.
        """
        try:
            chat = _build_chat(messages, baseline=baseline, persona=persona)
            prompt = self.tokenizer.apply_chat_template(
                chat,
                tokenize=False,
                add_generation_prompt=True,
            )
            inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
            with self._torch.inference_mode():
                out = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                    pad_token_id=self.tokenizer.eos_token_id,
                )
            generated = self.tokenizer.decode(
                out[0][inputs["input_ids"].shape[-1] :],
                skip_special_tokens=True,
            )
            # decode() of a single sequence returns str at runtime; the stub
            # widens it to str | list[str]. Narrow explicitly (no ignore needed).
            if isinstance(generated, list):
                generated = generated[0]
            obj = _parse_json_object(generated)
            if obj is None:
                return _neutral_delta()
            return _delta_from_json(obj)
        except Exception:
            return _neutral_delta()
