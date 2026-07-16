"""feltstate.memory.lifecycle.consistency — a zero-LLM immune hook for distilled memory.

Before a *distilled* memory (a fused crystal, a consolidated fact, any summary
written from other rows) is committed, check it against the **source rows it was
distilled from**, using only tokenisation, table lookups, regex, and arithmetic —
no model call anywhere in the decision. Adapted from a private companion's
crystal-verify gate; the rule there was *"rather drop it than let a fabrication
in."*

Six checks, each pass/fail:

1. ``anchor``    — enough of the summary's tokens appear in the source (whole-token
   lexical coverage).
2. ``splice``    — no single long clause is almost entirely unsupported. Runs only
   when ``anchor`` passed, so a heavily-paraphrased-but-true summary is not
   punished twice; it catches the *other* failure — a foreign clause spliced into
   an otherwise-supported summary (someone else's memory bleeding in).
3. ``numbers``   — every number in the summary appears in the source (numbers are
   the easiest thing to invent and the easiest to check).
4. ``negation``  — the summary's negation density is not far above the source's
   (guards against "it worked" being distilled into "it failed").
5. ``inflation`` — the summary is not much longer than its source (rewriting
   fragments into prose lengthens a little; fabrication balloons).
6. ``hollow``    — the summary has at least a few real content words.

A seventh, ``person`` (actor attribution), runs only when ``self_names`` is set:
the deed stays with whoever did it — the summary may not quietly move an action
from the other party onto "self", or the reverse.

Verdict from the fail count (thresholds match the source design):

* ``0`` fails            → ``ACCEPT``  (commit normally)
* ``1`` fail             → ``SUSPECT`` (commit, but flagged / down-weighted)
* ``>= reject_fails``    → ``REJECT``  (do not commit; the sources are still live,
  re-distill with different wording next pass)

Language: the default tokenizer splits on letter runs, which suits space-delimited
languages (English, most European). For a script without word spaces (Chinese,
Japanese, Thai, …) the word-based checks (anchor / splice / hollow) need real word
boundaries — pass your own segmenter as ``ConsistencyConfig.tokenize`` and your
own ``negations`` / ``stopwords`` / ``action_verb_pattern`` / pronouns. The check
*logic* is language-independent; only the tokenizer and tables change.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

ACCEPT = "accept"
SUSPECT = "suspect"
REJECT = "reject"

# English defaults. For another language, override these on ConsistencyConfig.
_EN_NEGATIONS = (
    "not", "no", "never", "none", "without", "n't", "cannot", "cant",
    "wont", "didnt", "wasnt", "isnt", "arent", "fail", "failed", "failure",
)  # fmt: skip
_EN_STOPWORDS = frozenset(
    "the a an is are was were be been being of to in on at and or but if then "
    "so as it its this that these those i you he she we they me him her us them "
    "my your his our their with for from by about into over under out up down "
    "do does did done have has had will would can could should may might".split()
)
# A pronoun immediately (within a couple of tokens) before an action verb is the
# attribution signal check 7 keys on. Defaults cover common English deed verbs.
_EN_ACTION_VERB = (
    r"(?:\w+\s+){0,2}(?:did|made|make|fixed|wrote|write|changed|change|built|"
    r"build|decided|decide|found|find|started|start|finished|deleted|delete|"
    r"added|add|asked|required|require|warned|warn|corrected|correct|hate|"
    r"hates|like|likes|want|wants|broke|shipped|removed)"
)

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_NUM_RE = re.compile(r"\d+")
_CLAUSE_SPLIT = re.compile(r"[，。；！？,;!?.…]+")


def _default_tokenize(text: str) -> list[str]:
    """Split into letter runs — suitable for space-delimited languages. For a
    script without word spaces, pass your own segmenter as ``tokenize``."""
    return _WORD_RE.findall(text or "")


@dataclass(frozen=True)
class ConsistencyConfig:
    """Thresholds (numeric, from the source design) + language tables (swap per
    language). Every field has an English-usable default."""

    anchor_min: float = 0.30
    clause_min: float = 0.12
    clause_len: int = 8
    neg_delta: float = 0.7
    inflate_max: float = 2.4
    min_content_words: int = 2
    reject_fails: int = 2
    suspect_heat_mult: float = 0.6
    viewpoint_min_len: int = 40

    tokenize: Callable[[str], list[str]] = _default_tokenize
    negations: tuple[str, ...] = _EN_NEGATIONS
    stopwords: frozenset[str] = _EN_STOPWORDS
    action_verb_pattern: str = _EN_ACTION_VERB
    self_pronoun: str = r"\bI\b"
    other_pronoun: str = r"\b(?:he|she|they|you)\b"
    # Actor names that count as "self". Empty → the person check is skipped.
    self_names: frozenset[str] = field(default_factory=frozenset)


DEFAULT_CONFIG = ConsistencyConfig()


def _tokens(text: str, cfg: ConsistencyConfig) -> list[str]:
    """All tokens, lower-cased (stopwords kept — the anchor counts them, as the
    source design does)."""
    return [t.lower() for t in cfg.tokenize(text or "")]


def _content(text: str, cfg: ConsistencyConfig) -> list[str]:
    """Content tokens: :func:`_tokens` minus stopwords."""
    return [t for t in _tokens(text, cfg) if t not in cfg.stopwords]


def _nums(text: str) -> set[str]:
    return set(_NUM_RE.findall(text or ""))


def _coverage(tokens: Sequence[str], pool: frozenset[str] | set[str]) -> float:
    """Fraction of ``tokens`` present in ``pool``. Empty ``tokens`` → 1.0 (nothing
    to disprove)."""
    if not tokens:
        return 1.0
    return sum(1 for t in tokens if t in pool) / len(tokens)


def _src_text(source_rows: Sequence[Mapping | str]) -> str:
    """Flatten source rows into one comparable string.

    Understands the complete 5W1H canon shape (actor, action, object, why, when,
    where) and falls back to ``str(row)`` for anything else, so it also accepts
    plain strings or foreign record shapes.
    """
    parts: list[str] = []
    for row in source_rows:
        if isinstance(row, Mapping):
            who = row.get("who") or {}
            what = row.get("what") or {}
            if isinstance(who, Mapping):
                parts.append(str(who.get("actor") or ""))
            elif who:
                parts.append(str(who))
            if isinstance(what, Mapping):
                parts.append(str(what.get("action") or ""))
                parts.append(str(what.get("object") or ""))
            elif what:
                parts.append(str(what))
            parts.append(str(row.get("why") or ""))
            parts.append(str(row.get("when") or ""))
            parts.append(str(row.get("where") or ""))
            if not (who or what or row.get("why") or row.get("when") or row.get("where")):
                parts.append(str(row))
        else:
            parts.append(str(row))
    return " ".join(p for p in parts if p)


def _neg_rate(text: str, negations: tuple[str, ...]) -> float:
    """Negation markers per ~10 characters (length-normalised)."""
    low = (text or "").lower()
    n = 0
    for marker in negations:
        if marker.isalpha():
            n += len(re.findall(rf"(?<!\w){re.escape(marker)}(?!\w)", low))
        else:
            n += low.count(marker)
    return n / max(1.0, len(low) / 10.0)


def check_consistency(
    text: str,
    source_rows: Sequence[Mapping | str],
    config: ConsistencyConfig | None = None,
    *,
    self_names: frozenset[str] | set[str] | None = None,
) -> dict:
    """Check a distilled ``text`` against the ``source_rows`` it was written from.

    Returns ``{"verdict": accept|suspect|reject, "fails": [names...],
    "detail": {...}}``. Never raises on ordinary input and never calls a model.
    ``self_names`` (if given) overrides ``config.self_names`` for the person check.
    """
    cfg = config or DEFAULT_CONFIG
    if self_names is not None:
        cfg = replace(cfg, self_names=frozenset(str(x).casefold() for x in self_names))

    src = _src_text(source_rows)
    if not src.strip():
        return {
            "verdict": REJECT,
            "fails": ["sources"],
            "detail": {"reason": "no usable source material"},
        }
    if not (text or "").strip():
        return {
            "verdict": REJECT,
            "fails": ["empty", "hollow"],
            "detail": {"reason": "empty distilled text"},
        }
    src_tokens = frozenset(_tokens(src, cfg))
    src_content = frozenset(_content(src, cfg))
    fails: list[str] = []
    detail: dict = {}

    # 1. anchor — whole-token lexical coverage of the summary by the source
    cover = _coverage(_tokens(text, cfg), src_tokens)
    detail["anchor"] = round(cover, 2)
    if cover < cfg.anchor_min:
        fails.append("anchor")

    # 2. splice — worst single long clause (only if anchor passed, no double jeopardy)
    if cover >= cfg.anchor_min:
        clauses = [
            cl for cl in _CLAUSE_SPLIT.split(text or "") if len(cl.strip()) >= cfg.clause_len
        ]
        scored = [(cl, _content(cl, cfg)) for cl in clauses]
        scored = [(cl, w) for cl, w in scored if len(w) >= 2]
        if scored:
            worst = min(_coverage(w, src_content) for _, w in scored)
            detail["clause_min"] = round(worst, 2)
            if worst < cfg.clause_min:
                fails.append("splice")

    # 3. numbers — every number in the summary must appear in the source
    stray = sorted(n for n in _nums(text) if n not in _nums(src))
    if stray:
        detail["stray_nums"] = stray
        fails.append("numbers")

    # 4. negation — summary far more negated than its source
    c_neg, s_neg = _neg_rate(text, cfg.negations), _neg_rate(src, cfg.negations)
    detail["neg"] = (round(c_neg, 2), round(s_neg, 2))
    if c_neg > s_neg + cfg.neg_delta:
        fails.append("negation")

    # 5. inflation — summary much longer than its source material
    if src:
        ratio = len(text or "") / max(1, len(src))
        detail["inflate"] = round(ratio, 2)
        if ratio > cfg.inflate_max:
            fails.append("inflation")

    # 6. hollow — too few distinct content words to carry meaning
    if len(set(_content(text or "", cfg))) < cfg.min_content_words:
        fails.append("hollow")

    # 7. person — actor attribution may not drift (opt-in via self_names)
    if cfg.self_names:
        self_name_set = frozenset(str(x).casefold() for x in cfg.self_names)
        actors = {
            str((row.get("who") or {}).get("actor") or "").casefold()
            for row in source_rows
            if isinstance(row, Mapping) and isinstance(row.get("who"), Mapping)
        } - {""}
        detail["actors"] = sorted(a for a in actors if a)
        self_verb = re.compile(cfg.self_pronoun + r"\W*" + cfg.action_verb_pattern, re.IGNORECASE)
        other_verb = re.compile(cfg.other_pronoun + r"\W*" + cfg.action_verb_pattern, re.IGNORECASE)
        if actors and actors <= self_name_set:
            if other_verb.search(text or ""):
                fails.append("person")  # self's deed attributed to the other party
            elif len(text or "") >= cfg.viewpoint_min_len:
                # Case (c): a long line about self, told wholly in the third
                # person with no first person — the verb can sit too far from the
                # pronoun for the deed-adjacency check above to catch, so fall
                # back to a pronoun census (viewpoint drift).
                self_n = len(re.findall(cfg.self_pronoun, text or "", re.IGNORECASE))
                other_n = len(re.findall(cfg.other_pronoun, text or "", re.IGNORECASE))
                if other_n >= 1 and self_n == 0:
                    fails.append("person")
        elif actors and not (actors & self_name_set):
            if self_verb.search(text or ""):
                fails.append("person")  # the other's deed attributed to self

    if len(fails) >= cfg.reject_fails:
        verdict = REJECT
    elif fails:
        verdict = SUSPECT
    else:
        verdict = ACCEPT
    return {"verdict": verdict, "fails": fails, "detail": detail}
