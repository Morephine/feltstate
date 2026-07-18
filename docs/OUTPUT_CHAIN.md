# The output chain — from reply text to a face and a voice

The library's contract ends at a `TurnResult`: the reply text, an
`emotion_label`, and the felt block that rode the prompt. Everything a user
*sees and hears* — the expression switch, the spoken line, the latency — is
built on that contract by the app. This page is the recommended chain, split
into what the library does (verifiable in code) and what your loop adds.

---

## 1. One turn, two signal channels

```text
                       ┌──────────────► face (FrontendAdapter)
        state edge ────┤   expression_signal(prev, new)
                       │   release flavour, else dominant mood label
reply ─┬─ text ────────┼──────────────► voice text (tags stripped)
       └─ [tag] ───────┴──────────────► voice colour (emotion_hint)
                           extract_emotion_tag(reply), mood fallback
```

Two channels, deliberately different sources (`companion/app.py`,
`companion/express.py`, `companion/round.py`):

* **The face follows the state, not the sentence.** `expression_signal()` is
  edge-triggered: the moment the pressure phase crosses into `releasing` it
  returns the release flavour (`tears`, `anger`, `burst_joy`, …); otherwise
  the dominant smoothed mood label. A face driven by persisted state cannot be
  puppeteered by one flowery sentence — it moves when the *feeling* moves.
* **The voice colour follows the reply.** The sentence-initial `[tag]` the
  model wrote is extracted (`extract_emotion_tag`), stripped from the spoken
  text (`_strip_tags`), and handed to the TTS adapter as `emotion_hint` —
  with the smoothed mood label as fallback when the model tagged nothing.

One tag, written once by the model, read twice by machinery — and the state
keeps veto power over the face. Apps that want per-sentence expressiveness can
*additionally* map tags to expressions (see §3); the state channel still
provides the baseline.

From the live example (`examples/companion_live.py`), the pair in the wild:

```text
  (her expression shifts: relieved)

ivy (joyful)> "That landed — I can tell it mattered. (feeling relieved)"
```

The face shifted on the state channel (`relieved` — the smoothed mood); the
voice coloured on the reply channel (`joyful` — the tag this sentence wore).
Two channels, visibly disagreeing by design: the face tracks how she *is*, the
voice tracks how she *says it*.

## 2. Latency: fire the voice on the first sentence

A companion that waits for the full reply before speaking feels like a page
load. The fix is an app-side streaming split:

1. stream the reply tokens from your backend;
2. hold a small buffer; the moment a sentence terminator closes the first
   sentence, hand that sentence to TTS and start playback;
3. keep synthesizing subsequent sentences while the first one plays;
4. extract the tag from the *first* sentence for the voice colour — it leads
   the reply precisely so the chain doesn't wait.

The library is indifferent to this split on purpose: `VoiceAdapter.synthesize`
takes text — call it per reply or per sentence, the contract is the same. The
tag convention (sentence-initial, `[word]`) is what makes per-sentence
splitting *clean*: each chunk can carry its own colour, and `_strip_tags`
already removes every tag from what gets spoken.

Interruption etiquette rides the same seam: `VoiceAdapter.should_speak` is the
gate — return `False` while the user is talking (VAD says so) and the line is
dropped, not queued into an awkward backlog.

## 3. Portability: the label is the protocol

Nothing in the chain names a renderer. The full journey of an expression is:

```text
label ("tears") ──► FrontendAdapter.label_to_token(label) ──► push_expression(token)
```

`label_to_token` is the *entire* mapping surface. Implementations seen in the
wild, in ascending exoticism:

| target | token | note |
|---|---|---|
| terminal demo | the label itself | `examples/companion_live.py` |
| Live2D | expression index / `.exp3.json` name | one dict, persona-specific |
| VRM / 3D avatar | blendshape preset name | same dict, different keys |
| third-party renderer you don't control | a **global hotkey** | a tiny bridge process maps label → keystroke; the renderer polls hotkeys — face and brain fully decoupled, no SDK required |
| a chat platform | an emoji reaction | the "face" of a text-only surface |

The last two are the point: because the signal is a plain label at a single
seam, the face can live in a different process — even a different program you
cannot modify — without the engine knowing or caring. Brain and body separate
cleanly at exactly this line.

## 4. Silent behaviours are part of the chain

Not every fired behaviour speaks. Dreams and introspection propose an **empty
payload**: the state work has already happened inside the source (a dream's
mood residue is applied by `Engine.maybe_dream` itself), and the dispatcher
returns before the turn machinery — an empty payload never reaches the voice
at all (`companion/app.py`, the `_proactive_say` early return).

`should_speak` is a different gate with two real jobs: skipping non-speakable
text (the default heuristic drops pure punctuation/whitespace) and carrying
your app's own muting — return `False` while the user is talking (VAD) and
the line is dropped, not queued.

A skin can still show *something* for the silent behaviours (a lamp, an idle
animation, a slow blink) by observing the scheduler's fires without voicing
them. Silence with a visible inner life reads as thinking; silence with a
frozen face reads as crashed — budget one visual for the former.

---

*See also:* [INTEGRATION.md](INTEGRATION.md) §6 for the adapter swap table,
and [PROMPT_STACK.md](PROMPT_STACK.md) §3 for why the same `[tag]` the voice
consumes also serves as the persona's forget probe.
