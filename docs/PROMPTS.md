# PROMPTS — contracts for the model seats

feltstate leaves a handful of seats open for a language model. Two ship with
their standing instructions built in — the **affect estimator**
(`LLMSource`, see `feltstate/sources/llm.py`) and the **fact extractor**
(`LLMFactExtractor`, see `feltstate/memory/extract.py`). Three are yours to
fill: the **namer** (keys), the **judge** (kinship), and the **summariser**
(the ladder's fused text). This chapter is the reference contract for each.

They are contracts, not vibes. One covenant runs through all of them:

> **Words are yours; numbers and time are never.** A model in any seat
> produces language and judgments. Timestamps come from the program.
> Salience, heat, decay, thresholds are computed, never dictated by prose.
> And an empty answer is a valid answer — nothing worth keeping is a
> result, not a failure.

A second habit shared by every seat: **read the whole slice before writing
anything.** A why written from one message is a guess; a why written after
reading the day is a cause.

---

## The namer — keys for one fact

Used at imprint time (`imprint_into`). The mechanical rules (single words,
no phrases, deduplication) are enforced by the library either way; the
prompt's job is *choosing words that will collide*.

```text
You name memories with keys so that related memories collide.

THE FACT
{actor} {action}: {object}
why: {why}

WORDS THIS LEDGER ALREADY SPEAKS (most used first)
{key_vocab output, comma-separated}

Rules:
- 2 to 4 keys, each a SINGLE word. A phrase never collides; it will be
  rejected mechanically anyway.
- Prefer a word from the ledger's list whenever it honestly fits. A brand
  new word is allowed only when nothing listed comes close.
- Name what the fact is ABOUT (the matter, the people, the place) — not how
  it felt. Feelings are recorded elsewhere.

Return ONLY a JSON array of strings.
```

Why the vocabulary is in the prompt: an extractor left alone mints a
bespoke, perfectly tailored phrase for every fact — each unique, therefore
silent. In the reference deployment, showing the working vocabulary moved
new-key-meets-old-key collision from ~2% to 40–60% within a week
(see `key_vocab`).

## The judge — is this kinship?

Used by the digest (`digest_canon(judge=...)`). Candidacy is arithmetic and
already done; the judge answers only the question arithmetic cannot.

```text
Two memories collided on shared keys. Decide whether they are truly kin.

A ({when_a}): {actor_a} {action_a}: {object_a} — why: {why_a}
B ({when_b}): {actor_b} {action_b}: {object_b} — why: {why_b}
shared keys: {shared}

Two votes:
1. same-matter: are these about the SAME ongoing matter — not merely the
   same topic word? ("both mention rent" is not "the same rent dispute".)
2. first-hand: is each memory a direct account, not a summary or hearsay
   of the other?

If unsure on either vote, the answer is no. Kinship written today is walked
by recall for years; a false edge misleads forever, a missing edge merely
waits for the next collision.

Return ONLY JSON: {"kin": true|false, "why": "<one short clause>"}
```

The `why` is stored on **both** rows and shown to future readers (hover an
edge in the dashboard); write it as the one clause you would want to find.

## The summariser — the ladder's fused text

Used by `ladder_pass(summarize=...)`. The consistency gate (`smelt`) will
lexically check the output against the members — unsupported numbers,
drifted actors, and hollow text are rejected, so stay close to the ground.

```text
{N} memories of the same theme are fusing into one {tier} memory.

MEMBERS (oldest first)
{one line per member: "{when}: {text}"}

Write the fused memory: 2–4 sentences, past tense, strictly grounded in the
members. Say what happened across them and how it ended or stands now.
Do not invent numbers, names, dates, or outcomes that no member states.
The arc matters more than the inventory: "a month of X ended in Y" beats
a list.

Return ONLY the fused text.
```

## The shipped seats, and retuning them

- **Affect estimator** (`LLMSource`): its standing instruction already
  guards the two classic failures — self-report (the character narrating
  itself into a mood) and mirroring (echoing the user's mood back). If you
  customise it, keep both guards and the fixed label list; the engine maps
  labels through fixed tables, so a free-form label is a dropped label.
- **Fact extractor** (`LLMFactExtractor`): keep the whole-slice framing and
  the *events, not sentences* bar — a dozen real events with causes beats a
  paraphrase of every message. Intensity (0..1) is the one number this seat
  may propose; it is an importance *judgment*, and the program still clamps
  and decays it. One economy worth naming: spend intelligence where it
  reads (extraction), spend pennies where it votes (judging).

Each seat degrades honestly without a model: rules fill the namer
(`examples/nightly.py`), shared-key counting fills the judge
(`SharedKeyJudge`), verbatim joining fills the summariser — crude on
purpose, so the machinery runs offline and the seats stay visibly seats.
