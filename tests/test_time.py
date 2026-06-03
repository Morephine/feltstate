"""Tests for feltstate.timeawareness.relative_time.

Two halves with two deliberate precisions:

* :func:`time_since_phrase` is *fuzzy* and stays silent (returns ``None``) for
  gaps under the gate — short spans are the model's own to feel — then coarsens
  with distance.
* :func:`now_phrase` is *precise*: weekday + part of day + clock.

Times are constructed explicitly so nothing depends on the wall clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from feltstate.config import DEFAULT_CONFIG
from feltstate.timeawareness import now_phrase, time_since_phrase

TCFG = DEFAULT_CONFIG.time
NOW = datetime(2030, 6, 5, 8, 11)  # a Wednesday morning, 8:11


def _phrase_for_gap(minutes: float):
    prev = NOW - timedelta(minutes=minutes)
    return time_since_phrase(prev.isoformat(), NOW, TCFG)


# --------------------------------------------------------------------------- #
# Gate: short gaps emit nothing                                               #
# --------------------------------------------------------------------------- #
def test_no_prior_timestamp_returns_none():
    assert time_since_phrase(None, NOW, TCFG) is None


def test_gap_below_gate_returns_none():
    # gate_minutes defaults to 30; anything under it is silent.
    assert _phrase_for_gap(5) is None
    assert _phrase_for_gap(29) is None


def test_gap_just_over_gate_starts_speaking():
    # 31 minutes is past the 30-minute gate -> a phrase appears.
    assert _phrase_for_gap(31) is not None


def test_unparseable_timestamp_returns_none():
    assert time_since_phrase("not-a-timestamp", NOW, TCFG) is None


# --------------------------------------------------------------------------- #
# The coarsening ladder                                                       #
# --------------------------------------------------------------------------- #
def test_ninety_minute_gap_lands_in_an_hour_or_so_band():
    # The ladder rung (90, "an hour or so") wins for gaps in [75, 90).
    assert _phrase_for_gap(85) == "an hour or so"


def test_phrases_coarsen_with_distance():
    # Sample a few rungs and confirm they match the configured ladder phrases.
    # (Each rung wins for gaps below its bound and at/above the previous one.)
    assert _phrase_for_gap(40) == "half an hour"
    assert _phrase_for_gap(120) == "almost two hours"
    assert _phrase_for_gap(150) == "a couple of hours"
    assert _phrase_for_gap(60 * 24 * 1.5) == "a day or so"  # ~1.5 days
    assert _phrase_for_gap(60 * 24 * 3) == "a few days"  # ~3 days
    assert _phrase_for_gap(60 * 24 * 8) == "about a week"  # ~8 days


def test_beyond_ladder_falls_back_to_absolute_day():
    # Far past the last rung (~75 days), it names the absolute day instead.
    prev = NOW - timedelta(days=200)
    phrase = time_since_phrase(prev.isoformat(), NOW, TCFG)
    assert phrase is not None
    assert phrase.startswith("back on ")
    # Should contain the month abbreviation of the prev date.
    assert prev.strftime("%b") in phrase


# --------------------------------------------------------------------------- #
# now_phrase: precise present                                                 #
# --------------------------------------------------------------------------- #
def test_now_phrase_contains_weekday_part_and_clock():
    s = now_phrase(NOW)
    # NOW is a Wednesday, 08:11 -> morning.
    assert s.startswith("Wed")
    assert "morning" in s
    assert "8:11" in s


def test_now_phrase_parts_of_day():
    # Map a few hours to their part-of-day label.
    assert "night" in now_phrase(NOW.replace(hour=3))  # pre-dawn
    assert "morning" in now_phrase(NOW.replace(hour=9))
    assert "afternoon" in now_phrase(NOW.replace(hour=14))
    assert "evening" in now_phrase(NOW.replace(hour=19))
    assert "night" in now_phrase(NOW.replace(hour=23))  # late evening


def test_now_phrase_uses_12_hour_clock():
    # 14:05 -> "2:05" on a 12-hour clock.
    s = now_phrase(NOW.replace(hour=14, minute=5))
    assert "2:05" in s


def test_now_phrase_unknown_locale_falls_back_to_english():
    # Reserved extension point: any non-"en" locale renders the English form.
    assert now_phrase(NOW, locale="xx") == now_phrase(NOW, locale="en")
