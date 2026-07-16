"""Tests for the zero-LLM distilled-memory consistency gate."""

from __future__ import annotations

from feltstate.memory.lifecycle import (
    ACCEPT,
    REJECT,
    SUSPECT,
    ConsistencyConfig,
    check_consistency,
)


def _row(actor: str, action: str, obj: str, why: str = "") -> dict:
    return {"who": {"actor": actor}, "what": {"action": action, "object": obj}, "why": why}


SRC = [
    _row("rin", "required", "tidy commits and no shortcuts"),
    _row("rin", "hates", "vague bug reports wants exact steps"),
    _row("rin", "corrected", "the version number mixup"),
]


def test_faithful_summary_accepts():
    r = check_consistency("he required tidy commits and hates vague reports, corrected the mixup", SRC)
    assert r["verdict"] == ACCEPT
    assert r["fails"] == []


def test_offtopic_with_invented_numbers_rejects():
    # low anchor + stray numbers = two fails = reject
    r = check_consistency("he flew to mars and drank 3 coffees with aliens in 2077", SRC)
    assert r["verdict"] == REJECT
    assert "anchor" in r["fails"] and "numbers" in r["fails"]


def test_stray_number_alone_is_suspect():
    r = check_consistency("he required tidy commits and hates vague reports in 2099", SRC)
    assert "numbers" in r["fails"]
    assert r["detail"]["stray_nums"] == ["2099"]


def test_hollow_summary_flagged():
    r = check_consistency("hmm", SRC)
    assert "hollow" in r["fails"]


def test_negation_flip_flagged():
    src = [_row("rin", "shipped", "the release it worked")]
    r = check_consistency("it did not ship, it never worked, no it failed", src)
    assert "negation" in r["fails"]


def test_person_check_skipped_without_self_names():
    # DEFAULT config has empty self_names -> no person judgement at all
    r = check_consistency("I required tidy commits and I corrected the mixup", SRC)
    assert "person" not in r["fails"]


def test_person_check_catches_stolen_deed():
    # sources are all "rin"; the summary claims the deed in first person -> person fail
    r = check_consistency(
        "I required tidy commits and I corrected the mixup",
        SRC,
        self_names={"astra"},
    )
    assert "person" in r["fails"]


def test_person_check_catches_viewpoint_drift():
    src = [_row("astra", "made", "real progress on the greenhouse automation")]
    # self's own experience, long, told entirely in the third person, no "I", and
    # the verb sits far from the pronoun so the deed-adjacency check can't catch it
    text = "the greenhouse automation finally came together and he seemed genuinely relieved about it"
    r = check_consistency(text, src, self_names={"astra"})
    assert "person" in r["fails"]


def test_config_thresholds_are_overridable():
    strict = ConsistencyConfig(anchor_min=0.99)
    r = check_consistency("he required foobar widgets", SRC, strict)
    assert "anchor" in r["fails"]


def test_pluggable_tokenizer_handles_unsegmented_script():
    # the default tokenizer cannot segment Chinese (no word spaces); passing a
    # tokenizer (here char-level; a real deployment would pass a proper segmenter)
    # makes the word-based checks work instead of false-failing anchor/hollow.
    zh = ConsistencyConfig(
        tokenize=lambda s: list(s or ""),
        stopwords=frozenset("的了是在有和跟就都还也这那你我他"),
    )
    src = [{"who": {"actor": "rin"}, "what": {"action": "讨厌", "object": "含糊的日程"}, "why": ""}]
    r = check_consistency("他讨厌含糊的日程", src, zh)
    assert "anchor" not in r["fails"]
    assert "hollow" not in r["fails"]
    assert r["verdict"] in (ACCEPT, SUSPECT)


def test_accepts_plain_string_sources():
    # source rows need not be 5W1H dicts; plain strings work too
    r = check_consistency("shipped the fix", ["we finally shipped the fix today"])
    assert r["verdict"] in (ACCEPT, SUSPECT)


def test_one_fail_is_suspect_not_reject():
    r = check_consistency("he required tidy commits in 2099", SRC)
    # a single stray number is the only fail -> suspect, not reject
    assert r["fails"] == ["numbers"]
    assert r["verdict"] == SUSPECT


def test_full_5w1h_fields_are_part_of_source_evidence():
    src = [
        {
            "who": {"actor": "rin"},
            "what": {"action": "met", "object": "sam"},
            "why": "conference",
            "when": "2026-07-14",
            "where": "Boston",
        }
    ]
    r = check_consistency("rin met sam in Boston on 2026-07-14 for the conference", src)
    assert "numbers" not in r["fails"]
    assert "anchor" not in r["fails"]


def test_negation_markers_use_word_boundaries():
    r = check_consistency("the notebook arrived", ["the notebook arrived"])
    assert r["detail"]["neg"] == (0.0, 0.0)


def test_self_names_match_case_insensitively():
    src = [_row("Astra", "fixed", "the build")]
    r = check_consistency("I fixed the build", src, self_names={"astra"})
    assert "person" not in r["fails"]


def test_no_usable_sources_is_rejected_structurally():
    r = check_consistency("invented memory", [])
    assert r["verdict"] == REJECT
    assert r["fails"] == ["sources"]
