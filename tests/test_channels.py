"""Tests for feltstate.render.channels — the surfaced-memory channel protocol.

Pinned: producers can only speak the closed channel set; payload newlines are
flattened (the protocol is line-oriented); parsing is strict — a known prefix
at position zero with the separator, nothing looser — because leniency is how
filters drift back into format-sniffing.
"""

from __future__ import annotations

import pytest

from feltstate.render.channels import CHANNELS, EMERGENT, FLASHBACK, parse, tag


def test_tag_speaks_the_closed_set_only():
    assert tag(EMERGENT, "she mentioned the maze again") == "EMG:she mentioned the maze again"
    assert tag("spk", "feeling first") == "SPK:feeling first"  # case-normalised producer side
    with pytest.raises(ValueError):
        tag("VIBES", "not a channel")  # a routing decision nobody made


def test_tag_flattens_newlines():
    assert tag(FLASHBACK, "line one\nline two") == "SPK:line one line two"


def test_parse_is_strict_and_round_trips():
    assert parse(tag(EMERGENT, "payload")) == ("EMG", "payload")
    assert parse("RSN:this is like last spring") == ("RSN", "this is like last spring")
    # Not protocol lines: narration, spacing drift, case drift, embedded match.
    for line in ("just narration", "EMG : spaced", "emg:lower", "xEMG:embedded", "", None, 42):
        assert parse(line) is None
    # Every advertised channel round-trips.
    for ch in CHANNELS:
        assert parse(f"{ch}:x") == (ch, "x")
