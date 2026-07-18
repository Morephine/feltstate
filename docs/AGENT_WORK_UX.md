# Working out loud — agent-work UX for a companion

When the reply backend is an *agent* — tool calls, retries, minutes of real
work — a companion hits two UX cliffs that a single-shot chatbot never meets:

1. **Silence reads as death.** From the chat window, ten focused minutes and a
   crashed process are indistinguishable.
2. **Telemetry reads as a machine.** Streaming raw tool logs at the user is
   worse: the character dissolves into a terminal.

The middle path is a **canned voicebank**: small pools of in-character lines
keyed by *event type*, sampled at random, throttled, with progress checkpoints
and distinct completion cues. The whole pattern is app-side — the library's
contract ends at the reply; how you narrate the *making* of the reply is your
loop's voice.

Everything quoted below is the deterministic output of
[`examples/agent_narration.py`](../examples/agent_narration.py) — run it.

---

## 1. The voicebank — pools per event, never one line per tool call

```text
  [t+   0s] [focused] Let me dig into that.
  [t+  12s] [smile] Found it.
  [t+  25s] [neutral] Let me think back.
  [t+  40s] [worried] Hm, that path didn't work — trying another way.
  [t+  55s] [curious] Searching around for that...
  [t+ 100s] [neutral] Step 10 — on track, this'll take a bit.
  [t+ 120s] [neutral] Nothing there. Moving on.
  [t+ 150s] [focused] Reading through it.
  [t+ 180s] [joy] Done — here's what I found.
```

Nine lines narrate ~15 events over three simulated minutes. The structure that
makes this read as a person working, not a log:

| pool | fires on | design note |
|---|---|---|
| `start` | entering a work stretch | one short acknowledgement — then the hands are busy |
| `tool.<act>` | each tool call | keyed by the *kind of act* (searching / reading), not the tool name; several phrasings per pool so repeats don't echo |
| `tool.memory` | memory lookups | a **thinking** pool, not a typing pool — digging through one's own notes is remembering, not operating a terminal |
| `result.ok / fail / empty` | tool outcomes | three pools, not one: success, failure, and *empty-handed* are three different feelings |
| `progress` | every N units of work | the "still alive" heartbeat, with an honest step count |
| `done.ok / done.fail` | end of the stretch | distinct from any mid-work line, so completion is unmistakable |

Every line carries its `[tag]` — the same tag protocol as normal replies, so
the face and voice colour keep working mid-task
([OUTPUT_CHAIN.md](OUTPUT_CHAIN.md)).

## 2. The throttle — the mouth rests, the hands don't

In the transcript above, tool calls fired at t+2, t+4 and t+6 — and produced
*no* lines: a minimum gap (8 s in the demo; 20–40 s is right for real work)
suppressed the burst. The rules:

* **Narration is best-effort, work is not.** A throttled line is dropped, never
  queued — stale narration ("searching...", said after the search ended) is
  worse than none.
* **Start, completion, and failure force through** the throttle. The lines
  that carry state transitions must never be dropped.
* **Progress counts are honest.** "Step 10" means ten real units of work — a
  count the user can feel, not decoration.

## 3. Carrying work across turns

Long work should survive the turn boundary rather than blocking it: hand the
job to a background worker, answer the current turn with the `start` line, and
deliver the result later **through the proactive path** — the same
propose/dispatch/commit pipe that carries pending topics
([INTEGRATION.md](INTEGRATION.md) §4). Dropping the finished result into the
topics store is the simplest wiring: the companion raises it the moment the
gates allow, in her own voice, even if the user wandered off for an hour.

Two disciplines keep this honest:

* **One mouth.** Background work reports through the companion, never around
  her — if workers can post directly to the user's surface, the character
  fractures into a swarm ([INTEGRATION.md](INTEGRATION.md) §7 applies).
* **Batch the endings.** Ten sub-tasks finishing = one summary line, not ten
  pings. Completion is a *feeling*, not a webhook.

## 4. What not to do

* Don't echo tool names or arguments — "running grep -rn" is telemetry, not
  speech. Narrate the act: "searching around for that".
* Don't narrate every event — the throttle is the personality. A person deep
  in work says little.
* Don't reuse a mid-work line as the completion line — the user needs to hear
  the difference without reading closely.
* Don't fake the counts, and don't let the narration outlive the work: if the
  job died, the next line must be a failure line
  ([FAILURE_IN_CHARACTER.md](FAILURE_IN_CHARACTER.md)), not another cheery
  "still going".
