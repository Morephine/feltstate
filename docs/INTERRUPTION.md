# Interruption — being cut off gracefully

Real conversation is interruptible. You talk over someone, they stop, the
floor changes hands — nobody reboots. A companion you cannot cut off
mid-sentence is a kiosk playing announcements. This page covers the two
interrupt paths (voice and text), what a stop must actually kill, and the
recovery posture afterwards — the part most implementations get wrong.

Like [BRIDGE_ETIQUETTE.md](BRIDGE_ETIQUETTE.md), this is an app-side pattern
chapter; §4 lists the exact library seams it hangs on.

---

## 1. Voice barge-in, without headphones

The hard version of voice interruption is speakers + open mic: her own voice
comes back through the microphone, so a naive VAD interrupts her with
*herself*. The solutions, in ascending effort:

1. **Half-duplex gating** — raise the VAD trigger threshold (or require
   sustained speech, not a spike) while TTS is playing. Cheap, ships first,
   occasionally misses a soft-spoken interrupt.
2. **Echo cancellation** — subtract the known playback signal from the mic
   input (AEC), then run VAD on the residue. The real fix; most OS audio
   stacks expose it.
3. **Keyword-gated barge-in** — only a wake word interrupts during playback.
   Robust but least natural; a fallback for hopeless acoustics.

Whichever detector fires, the response is the same and it is *immediate*:

* stop playback **now** — mid-word, not at the sentence boundary; a companion
  that finishes her clause after you started talking isn't listening;
* flush the synthesis queue — sentences already synthesized but unplayed die
  with the turn (the first-sentence streaming split from
  [OUTPUT_CHAIN.md](OUTPUT_CHAIN.md) §2 means there usually *is* a queue);
* mark the turn interrupted in history — the unsaid tail is **never replayed
  later**. What she didn't get to say stays unsaid.

## 2. The text stop chain

On a bridge, the stop command lives in the emergency lane
([BRIDGE_ETIQUETTE.md](BRIDGE_ETIQUETTE.md) §4) — parsed by the bridge
process, never by the model, so it works while the model is the problem.
A stop is only real if it kills the whole chain, in order:

```text
!stop ──► 1. audio playback        (silence, immediately)
          2. TTS synthesis queue   (pending sentences dropped)
          3. the in-flight turn    (backend request cancelled / killed)
          4. pending narration     (queued progress lines dropped)
          receipt: one mechanical ack from the lane — not a spoken sentence
```

Killing 1 without 3 produces the classic zombie: she goes quiet, the backend
keeps burning, and the *next* turn collides with the corpse. Two properties
are non-negotiable: **idempotent** (a second `!stop` into an already-stopped
system reports "nothing running" — it never errors) and **acknowledged by the
lane, not the character** (a model-generated apology is exactly the machinery
you just asked to stop).

## 3. Stop *talking* is not stop *working*

Two intents, two commands. Cutting off a sentence should not cancel a
twenty-minute background job; aborting the job shouldn't require shouting
over her. Map them separately:

| intent | scope | afterwards |
|---|---|---|
| stop talking (barge-in, soft stop) | playback + synthesis queue | the turn's *work* survives; results arrive quietly |
| stop working (`!stop`-class) | the whole chain of §2 | the task is dead; `interrupted` line acknowledges once |

Voice barge-in defaults to *stop talking* — the user usually wants the floor,
not an abort. Escalation to *stop working* is an explicit command.

## 4. The recovery posture

What happens on the next turn decides whether interruption feels natural:

* **Never resume the cut sentence.** "As I was saying…" is a tape rewinding —
  it tells the user their interrupt was an inconvenience to the machine
  rather than a turn in a conversation.
* **The user's content leads.** They interrupted *to say something*; the next
  reply answers that, not the abandoned thread. If the dropped topic still
  matters, she can raise it later — as a new thought, not a resumption.
* **At most one clause of acknowledgement**, from the `interrupted` felt
  failure kind ([FAILURE_IN_CHARACTER.md](FAILURE_IN_CHARACTER.md) §1):
  "Okay — dropping that." No apology spiral; recovery etiquette
  (ibid. §4) applies verbatim.
* **The event may enter the state.** Being cut off is a user act like any
  other — the app can route it through the normal appraisal path as a mild
  signal. What it must never do is write state *instructions* ("be hurt");
  description-not-instruction holds even here.

## 5. The library seams it hangs on

| seam | where | role in interruption |
|---|---|---|
| `VoiceAdapter.should_speak(text)` | `companion/voice.py` | app-owned mute: return `False` while the user is talking and the line is dropped, not queued |
| the async turn lock | `companion/app.py` (`_async_turn_lock`) | turns serialize — a killed turn releases the lock and the next one starts clean, no interleaved half-turns |
| `presence.is_busy()` → scheduler gate | `companion/gates.py`, checked in `companion/scheduler.py` | proactive fires hold while a conversation is live — the scheduler never talks over the user by design |
| the `interrupted` canned line | `examples/agent_narration.py` (`FAILURE_LINES`) | the one-clause acknowledgement, pre-written so the dead turn doesn't need a live model to bow out |

Everything else — VAD, AEC, playback control, the kill chain — is
deliberately outside the library: it is transport, and transports vary. The
library's contribution is that its surfaces (`should_speak`, one lock, one
gate, canned lines) are shaped so a hard kill anywhere leaves the state
consistent and the character continuous.
