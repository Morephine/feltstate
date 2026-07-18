# Failure, in character — errors that don't break the fourth wall

A companion crashes differently than a CLI. A stack trace in the chat window
doesn't just look bad — it momentarily *deletes the character*: the user was
talking to someone, and suddenly there's machinery on the table. The rule this
page defends:

> **Two audiences, two truths.** The user hears a person having trouble.
> The operator's log keeps the real exception. Neither audience ever
> receives the other one's version.

Demonstrated by section B of
[`examples/agent_narration.py`](../examples/agent_narration.py):

```text
  (logged for the operator: ConnectionError: [Errno 101] Network is unreachable)
  spoken to the user:  [worried] I can't reach the outside world right now — the network's gone quiet. Try me again in a bit?

  (logged for the operator: TimeoutError: upstream inference timed out after 300s)
  spoken to the user:  [worried] My train of thought stalled mid-sentence. Ask me that once more?
```

---

## 1. Diagnose first, then speak

Don't map exception *classes* to lines — map **felt failure kinds**. The user
doesn't care which layer threw; they care whether the world is broken or she
is:

| kind | typical evidence | the line's job |
|---|---|---|
| `net_down` | connection errors; a cheap probe of a known host fails | "the world is unreachable" — blame the weather, invite retry later |
| `model_stalled` | timeouts, upstream 5xx, a turn that produced nothing | "my thought stalled" — own it lightly, invite an immediate retry |
| `interrupted` | the user cancelled / a stop command landed | acknowledge and drop it — no apology spiral |

A one-line diagnosis function (`diagnose()` in the example) is enough. When
evidence is ambiguous, a **cheap active probe** settles it — try one known
endpoint with a short timeout; reachable means the fault is internal. The
distinction matters because the two lines assign blame differently, and
misassigning it ("I'm broken" when the wifi died) erodes exactly the trust the
in-character line exists to protect.

## 2. The delivery rules

* **Tagged like any speech.** Failure lines carry `[worried]` / `[neutral]` —
  the face and voice colour stay in the loop while things go wrong
  ([OUTPUT_CHAIN.md](OUTPUT_CHAIN.md)). A companion that goes poker-faced at
  the moment of failure reads as *more* broken.
* **One line, then quiet.** No retry-storm narration, no apology per attempt.
  If recovery is automatic, the next line is the result; if not, the line
  already told the user what to do.
* **Force through the throttle.** Like start/completion lines
  ([AGENT_WORK_UX.md](AGENT_WORK_UX.md) §2), a failure line is a state
  transition — it must never be dropped as narration spam.
* **The log gets everything.** Class, message, stack, timing — verbatim, at
  the operator surface only. In-character is a presentation layer, not
  information destruction.

## 3. The watchdog case — dying mid-work, still in voice

The hardest failure is the one the turn can't report itself: the backend hangs
and produces *nothing*. The pattern:

1. a watchdog arms when the turn starts (pick a deadline generous enough for
   honest thinking);
2. on expiry with zero output, it kills the stuck turn — and **speaks the
   failure itself**, from the same canned bank, in her voice: run the
   `net_down` probe to choose between "the network's gone quiet" and "my mind
   stalled — ask me again";
3. the loop resets so the next user message starts clean.

The system speaking *as her* at the exact moment she can't speak for herself
is what keeps the character continuous through a hard kill. The alternative —
a platform-flavoured error toast — is the fourth wall collapsing at the worst
possible time.

## 4. Recovery etiquette

On the next successful turn, at most a clause of acknowledgement ("back —
that one got away from me") and only if the failure was *visible*. No
grovelling, no re-explaining the outage. People who recover gracefully don't
re-live the stumble; neither should she.
