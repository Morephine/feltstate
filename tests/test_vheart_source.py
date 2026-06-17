"""VheartSource — fine-tuned adapter source. The contract under test:

* Import the module without the ``vheart`` extra installed.
* Construct without the extra → clear ``RuntimeError`` (not a cryptic ImportError).
* Coerce a clean JSON output into an :class:`AffectDelta`.
* Drop a malformed ``mixed_blend`` rather than passing junk through.
* Never raise from anywhere reachable during a normal ``read()``: tokenizer
  failure, model failure, parse failure → near-neutral reading.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

# The module under test imports torch et al *inside* __init__, so the bare
# import below must succeed even when the extras aren't installed.
from feltstate.sources import vheart
from feltstate.state import AffectDelta, AffectState


# --------------------------------------------------------------------------- #
# Module import is cheap                                                      #
# --------------------------------------------------------------------------- #
def test_module_imports_without_extras():
    assert hasattr(vheart, "VheartSource")
    assert hasattr(vheart, "_parse_json_object")
    assert hasattr(vheart, "_delta_from_json")


# --------------------------------------------------------------------------- #
# Constructor surfaces a clear error when extras are missing                  #
# --------------------------------------------------------------------------- #
def _block_import(*blocked_prefixes: str):
    """Return a fake `__import__` that raises ImportError on selected modules."""
    real_import = __import__

    def fake_import(name, *a, **kw):
        for p in blocked_prefixes:
            if name == p or name.startswith(p + "."):
                raise ImportError(f"{name} blocked by test")
        return real_import(name, *a, **kw)

    return fake_import


def test_constructor_raises_clear_runtime_error_without_torch(monkeypatch):
    """If torch isn't importable, the constructor must raise RuntimeError
    naming the install command — not a raw ImportError from deep in the
    transformers stack."""
    monkeypatch.setattr("builtins.__import__", _block_import("torch"))
    with pytest.raises(RuntimeError) as exc:
        vheart.VheartSource("kaishuiji/vheart-affect-v9")
    assert "vheart" in str(exc.value).lower()


def test_constructor_raises_clear_runtime_error_without_huggingface_hub(monkeypatch):
    """huggingface_hub is part of the [vheart] extra. Missing it must also
    produce the same clear RuntimeError, not a raw ImportError."""
    monkeypatch.setattr("builtins.__import__", _block_import("huggingface_hub"))
    with pytest.raises(RuntimeError) as exc:
        vheart.VheartSource("kaishuiji/vheart-affect-v9")
    assert "vheart" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# JSON parsing                                                                #
# --------------------------------------------------------------------------- #
def test_parse_json_object_clean():
    obj = vheart._parse_json_object(
        '{"valence":0.4,"arousal":0.5,"labels":["focused"]}'
    )
    assert isinstance(obj, dict) and obj["valence"] == 0.4


def test_parse_json_object_wrapped_in_prose():
    obj = vheart._parse_json_object(
        'Here is the measurement: {"valence":-0.2,"arousal":0.3} done.'
    )
    assert obj == {"valence": -0.2, "arousal": 0.3}


def test_parse_json_object_garbage_returns_none():
    assert vheart._parse_json_object("nope") is None
    assert vheart._parse_json_object("") is None
    assert vheart._parse_json_object("[1,2,3]") is None  # array, not object


# --------------------------------------------------------------------------- #
# Delta coercion                                                              #
# --------------------------------------------------------------------------- #
def test_delta_from_json_clean():
    d = vheart._delta_from_json(
        {
            "valence": 0.6,
            "arousal": 0.5,
            "labels": ["grateful"],
            "confidence": 0.8,
            "monologue": "warm",
        }
    )
    assert isinstance(d, AffectDelta)
    assert d.valence == 0.6 and d.arousal == 0.5
    assert d.labels == ["grateful"] and d.confidence == 0.8
    assert d.monologue == "warm"


def test_delta_from_json_clamps_out_of_range():
    d = vheart._delta_from_json({"valence": 5.0, "arousal": -1.0, "confidence": 9.0})
    assert d.valence == 1.0
    assert d.arousal == 0.0
    assert d.confidence == 1.0


def test_delta_from_json_handles_string_floats():
    d = vheart._delta_from_json({"valence": "0.25", "arousal": "0.7"})
    assert d.valence == 0.25 and d.arousal == 0.7


def test_delta_from_json_labels_cap_at_three():
    d = vheart._delta_from_json(
        {"labels": ["a", "b", "c", "d", "e"]}
    )
    assert d.labels == ["a", "b", "c"]


def test_delta_from_json_label_csv_string():
    d = vheart._delta_from_json({"labels": "focused, curious , "})
    assert d.labels == ["focused", "curious"]


def test_delta_from_json_garbage_returns_neutral():
    d = vheart._delta_from_json("not a dict")
    assert d.valence == 0.0 and d.confidence == 0.1


# --------------------------------------------------------------------------- #
# mixed_blend validation                                                      #
# --------------------------------------------------------------------------- #
def test_mixed_blend_passes_clean_shape():
    d = vheart._delta_from_json(
        {
            "mixed_blend": {
                "primary": "proud",
                "secondary": "relieved",
                "weights": [0.6, 0.4],
            }
        }
    )
    assert d.mixed_blend == {
        "primary": "proud",
        "secondary": "relieved",
        "weights": [0.6, 0.4],
    }


def test_mixed_blend_drops_bad_shapes():
    bad_inputs = [
        {"mixed_blend": "frustrated"},                                # string
        {"mixed_blend": {"primary": "x"}},                            # missing keys
        {"mixed_blend": {"primary": "x", "secondary": "y"}},          # missing weights
        {"mixed_blend": {"primary": "x", "secondary": "y", "weights": [0.5]}},  # wrong len
        {"mixed_blend": {"primary": 1, "secondary": "y", "weights": [0.5, 0.5]}},  # non-str
        # Non-numeric weights — must NOT silently fall back to 0.5 / 0.5.
        {"mixed_blend": {"primary": "a", "secondary": "b", "weights": ["bad", "alsobad"]}},
        {"mixed_blend": {"primary": "a", "secondary": "b", "weights": [None, 0.5]}},
        {"mixed_blend": {"primary": "a", "secondary": "b", "weights": [object(), 0.5]}},
        # booleans are not real numbers for this field.
        {"mixed_blend": {"primary": "a", "secondary": "b", "weights": [True, 0.5]}},
    ]
    for inp in bad_inputs:
        d = vheart._delta_from_json(inp)
        assert d.mixed_blend is None, f"should reject: {inp}"


def test_mixed_blend_weights_clamped():
    d = vheart._delta_from_json(
        {"mixed_blend": {"primary": "a", "secondary": "b", "weights": [2.0, -1.0]}}
    )
    assert d.mixed_blend == {
        "primary": "a",
        "secondary": "b",
        "weights": [1.0, 0.0],
    }


# --------------------------------------------------------------------------- #
# Neutral delta is always a fresh instance                                    #
# --------------------------------------------------------------------------- #
def test_neutral_delta_is_fresh_each_call():
    a = vheart._neutral_delta()
    b = vheart._neutral_delta()
    assert a is not b
    a.labels.append("contaminated")
    assert b.labels == []  # mutation of a does not leak into b


# --------------------------------------------------------------------------- #
# read() never raises — stub the heavy parts                                  #
# --------------------------------------------------------------------------- #
class _FakeTensor:
    """Minimal duck-type for the tensor of input_ids the stub returns."""

    def __init__(self, n_tokens: int = 4):
        # transformers code reads inputs["input_ids"].shape[-1].
        self.shape = (1, n_tokens)


class _FakeBatch(dict):
    """Acts as both a mapping (** unpacking) and an object with .to(...)."""

    def __init__(self):
        super().__init__()
        self["input_ids"] = _FakeTensor()

    def to(self, device):
        return self


class _StubTokenizer:
    eos_token_id = 0

    def apply_chat_template(self, chat, **kw):
        return "stub-prompt"

    def __call__(self, prompt, return_tensors=None):
        return _FakeBatch()

    def decode(self, ids, skip_special_tokens=True):
        return '{"valence":0.3,"arousal":0.5,"labels":["focused"]}'


class _StubModel:
    def generate(self, **kw):
        # Return tensor-like with a [0] indexable to a sliceable.
        return [[0] * 10]


def _make_stub_source():
    src = vheart.VheartSource.__new__(vheart.VheartSource)
    src.tokenizer = _StubTokenizer()
    src.model = _StubModel()
    src.device = "cpu"
    src.max_new_tokens = 64

    class _StubTorch:
        class _InferenceCtx:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

        @staticmethod
        def inference_mode():
            return _StubTorch._InferenceCtx()

    src._torch = _StubTorch
    return src


def test_read_returns_parsed_delta_on_clean_path():
    src = _make_stub_source()
    d = src.read([{"role": "user", "content": "hi"}], baseline=AffectState())
    assert d.valence == 0.3
    assert d.labels == ["focused"]


def test_read_returns_neutral_on_tokenizer_failure():
    src = _make_stub_source()
    src.tokenizer = None  # any attribute access blows up
    d = src.read([{"role": "user", "content": "hi"}], baseline=AffectState())
    assert d.valence == 0.0 and d.confidence == 0.1
    # Must be a fresh instance, not the singleton-pattern bug.
    d.labels.append("scratch")
    d2 = src.read([{"role": "user", "content": "again"}], baseline=AffectState())
    assert d2.labels == []


def test_read_returns_neutral_on_model_failure():
    src = _make_stub_source()

    class _BoomModel:
        def generate(self, **kw):
            raise RuntimeError("CUDA OOM (simulated)")

    src.model = _BoomModel()
    d = src.read([{"role": "user", "content": "hi"}], baseline=AffectState())
    assert d.valence == 0.0 and d.confidence == 0.1


def test_read_returns_neutral_on_unparseable_output():
    src = _make_stub_source()

    class _GarbageTok(_StubTokenizer):
        def decode(self, ids, skip_special_tokens=True):
            return "I refuse to output JSON, here are some thoughts instead."

    src.tokenizer = _GarbageTok()
    d = src.read([{"role": "user", "content": "hi"}], baseline=AffectState())
    assert d.valence == 0.0 and d.confidence == 0.1


# --------------------------------------------------------------------------- #
# Constructor wiring (mocked) — the real failure path that earlier tests skipped #
# --------------------------------------------------------------------------- #
def test_constructor_resolves_base_model_from_adapter_config(monkeypatch, tmp_path):
    """When ``base_model`` is omitted, the constructor reads
    ``base_model_name_or_path`` out of the adapter's ``adapter_config.json``
    via huggingface_hub.hf_hub_download. Cover that wiring with mocks so
    the test does not touch the network.
    """
    # Adapter config the mocked hub will hand back.
    cfg_path = tmp_path / "adapter_config.json"
    cfg_path.write_text(json.dumps({"base_model_name_or_path": "stub/base"}))

    # Lightweight stubs for the heavy modules. The constructor only
    # touches a few attributes on each, so the stubs stay tiny.
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
        float16=object(),
        float32=object(),
        inference_mode=lambda: _FakeNullContext(),
    )

    class _FakeBase:
        def to(self, device):
            return self

    fake_transformers = types.SimpleNamespace(
        AutoModelForCausalLM=types.SimpleNamespace(
            from_pretrained=lambda *a, **kw: _FakeBase(),
        ),
        AutoTokenizer=types.SimpleNamespace(
            from_pretrained=lambda *a, **kw: _StubTokenizer(),
        ),
    )
    fake_peft = types.SimpleNamespace(
        PeftModel=types.SimpleNamespace(
            from_pretrained=lambda base, adapter: _StubModelWithEval(),
        ),
    )
    fake_hub = types.SimpleNamespace(
        hf_hub_download=lambda repo_id, filename: str(cfg_path),
    )

    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setitem(sys.modules, "peft", fake_peft)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    src = vheart.VheartSource("stub/adapter")
    assert src.device == "cpu"
    assert src._torch is fake_torch
    # Sanity check: read() still works through the stubbed model.
    src.tokenizer = _StubTokenizer()
    src.model = _StubModel()
    d = src.read([{"role": "user", "content": "hi"}], baseline=AffectState())
    assert d.valence == 0.3


def test_constructor_raises_when_adapter_config_has_no_base(monkeypatch, tmp_path):
    cfg_path = tmp_path / "adapter_config.json"
    cfg_path.write_text(json.dumps({}))  # no base_model_name_or_path

    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
        float16=object(),
        float32=object(),
    )
    fake_transformers = types.SimpleNamespace(
        AutoModelForCausalLM=types.SimpleNamespace(from_pretrained=lambda *a, **kw: None),
        AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *a, **kw: None),
    )
    fake_peft = types.SimpleNamespace(
        PeftModel=types.SimpleNamespace(from_pretrained=lambda *a, **kw: None),
    )
    fake_hub = types.SimpleNamespace(
        hf_hub_download=lambda repo_id, filename: str(cfg_path),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setitem(sys.modules, "peft", fake_peft)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    with pytest.raises(RuntimeError) as exc:
        vheart.VheartSource("stub/adapter")
    assert "base_model" in str(exc.value).lower()


class _FakeNullContext:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _StubModelWithEval(_StubModel):
    def eval(self): return self
