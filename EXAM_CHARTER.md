# The Memory Physical — a testing philosophy

Most agent-memory evaluation asks one question: *given a query, can the system
find the fact?* Necessary, and nowhere near sufficient for a companion — an
agent whose memory is supposed to feel like a lived past, not a database. We
test our own memory system along four axes, and we think any long-running
companion deserves the same physical. This note states the idea only; it is a
testing philosophy, not a benchmark spec.

**1. Recall — is what was stored findable?** The classical axis, already well
served by public benchmarks (LoCoMo, LongMemEval and successors). Still worth
a small in-house paper, because it should run against the agent's *own lived
store*, not a synthetic corpus.

**2. Surfacing — does the right memory come up on its own, and does it fit?**
Companions don't wait to be queried; memory surfaces proactively. A remark
about the sea should sometimes pull up last month's harbor walk, and never an
unrelated grievance. Related work is emerging (proactive-retrieval and
memory-association benchmarks); the version we care about grades the fitness
of what actually surfaced, in scene context, on the lived store — because this
is the axis that decides whether spontaneity reads as *a mind remembering* or
as a retriever misfiring.

**3. Confabulation defense — can the store be poisoned?** Memory systems
hallucinate at write time (HaluMem measures this well on synthetic pipelines).
The in-vivo version: inject false material at every gate the real system
exposes — extraction, consolidation, "you said yesterday..." corrections — and
measure what reaches the durable store. An agent that can be gaslit into
remembering what never happened fails, however well it retrieves.

**4. Aging — does the store grow old the way it was designed to?** Push the
whole store forward on paper (+7, +30, +90, +365 days) with the system's own
clocks and audit the projected population: do the kinds keep their designed
ordering, does anything undeclared live forever, does mass stay bounded. Decay
design is easy to write and easy to silently break; this catches the break
without waiting a year.

The four are one instrument: a store can ace recall while flunking surfacing
(a good database, a bad mind), or defend perfectly while aging into a hoard.
Run all four on the same lived store at every meaningful change, and watch the
trend line, not the single grade.
