# Contributing to feltstate

Thanks for your interest. feltstate is small on purpose — a clean reference
implementation of a specific set of ideas (see [PHILOSOPHY.md](PHILOSOPHY.md)).
Contributions that sharpen the core, fix bugs, or improve clarity are very
welcome; please keep the scope discipline below in mind.

## Setup

```bash
git clone <your-fork-url>
cd feltstate
python -m pip install -e ".[dev]"   # editable install + pytest, ruff, mypy
```

Requires Python 3.10+.

## The checks (all run in CI)

Run them locally before opening a PR — they must all pass:

```bash
ruff check .          # lint
ruff format .         # format (or `ruff format --check .` to verify)
mypy feltstate        # type check
pytest -q             # tests
```

## Design constraints (please respect these)

These are what keep feltstate itself rather than a generic library:

1. **The core stays dependency-free.** `feltstate/` (engine, affect, memory,
   render, time, `KeywordSource`, `LLMSource`) must run on the standard library
   alone. New third-party dependencies belong behind an optional `AffectSource`
   you ship separately, not in the core.
2. **Tool, not controller.** Code produces *state* and renders it; it never
   injects an instruction ("be sad now") into the prompt. If you're tempted to
   add a behavioural rule to the output, that's a smell — see PHILOSOPHY.md §2.
3. **Keep the render cache-stable.** `render_felt_block` must stay coarse-banded
   so adjacent ticks render byte-identically. Prefer a handful of wide phrase
   bands over many narrow ones; never put raw numbers in the rendered text.
4. **Character-agnostic.** No personas, no real conversational data, no
   language-specific corpora in the package. Personality is supplied by the
   caller via `PersonaDials` and persona text, not baked into code.
5. **Sources never raise.** An `AffectSource.read` that can't get a clean signal
   returns a low-confidence neutral delta — a bad reading must look like "no
   clear signal", never crash the agent.

## Adding an AffectSource

Subclass `feltstate.sources.base.AffectSource` and implement
`read(messages, *, baseline, persona) -> AffectDelta`. Weight the latest *user*
turn (not the agent's own words), react from the baseline rather than mirroring
the user, and never raise. See `KeywordSource` / `LLMSource` for the two
reference shapes.

## Tests

Every behavioural change needs a test. Tests are plain `pytest`, no external
services (the `LLMSource` tests stub the HTTP call — keep it that way; CI has no
network).

## Commits & PRs

Small, focused commits with a clear message. Describe *why*, not just *what*.
By contributing you agree your work is licensed under the project's MIT license.
