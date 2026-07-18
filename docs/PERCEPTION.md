# Perception — how images and screens enter a turn

A companion that can be *shown* things — a photo, a screenshot, whatever is on
screen right now — feels present in a way a text-only one never does. This
page is the input-side multimodality chapter: the inbound image path, the
pull-eye pattern for voluntary looking, and the one layering rule that keeps
perception from corrupting the affect layer.

---

## 1. The layering rule: perception is input, not state

feltstate never sees a pixel. The engine's surfaces are text
(`Engine.inject(user_message)`, appraisal sources reading the exchange), and
that is a feature: an image changes how the companion feels only **through the
conversation it produces** — her read of it, the exchange about it — appraised
by the same sources that appraise everything else.

The anti-pattern this forbids: an app-side classifier deciding "sad image →
bump sadness". That is instruction dressed as perception — the same fault as
writing "be sad" into the prompt (README, design choice #6), one layer down.
If seeing something moves her, the movement arrives through appraisal of the
turn, with appraisal's own trust dial — never as a direct write.

## 2. The inbound path: persist → perceive → reply

Step one is always the same and comes from
[BRIDGE_ETIQUETTE.md](BRIDGE_ETIQUETTE.md) §3: the file lands on local disk
first, and is perceived *from the local copy* — never re-uploaded, never
forwarded. From there, two shapes depending on your backend:

**Multimodal backend** — the image joins the turn's content directly:

```text
content of the newest user turn:
  [text]  the felt block                     ← engine.render()
  [image] the persisted local file
  [text]  the user's words (caption, or "")
```

`Engine.inject()` is documented as a thin wrapper over `render()` +
the injection builder — so when your turn content is an *array* rather than a
plain string, call `render()` yourself and assemble. The sandwich discipline
([PROMPT_STACK.md](PROMPT_STACK.md)) is modality-blind: felt block first,
world last, static prefix untouched — an image turn caches exactly like a
text turn.

**Caption bridge** — for a text-only backend, a separate vision pass first
describes the image, and the description enters the turn *marked as
perception, not speech*:

```text
[you are looking at: a whiteboard covered in half-erased equations,
 someone's phone number in the corner]
...user's actual words...
```

The bracket matters: an unmarked caption reads as something the user *said*,
and appraisal will treat it that way. Perception gets a frame that says
"seen, not heard."

## 3. The pull eye: looking as an act

Attachments are push — the user decides she sees. The complementary pattern
gives her an eye she *chooses* to use: the shell keeps one local file
perpetually fresh (a screen mirror refreshed every second or so; a webcam
frame; a game-state snapshot — anything), and the companion reads it when
asked to look, or when she's curious. **The eye is a file.**

Why pull beats push, in order of importance:

1. **Looking is characterful.** "Let me see" followed by an act of looking is
   behaviour; an omnipresent video feed is surveillance. Choice-to-look reads
   as a person, and *declining* to look stays possible.
2. **Cost.** A frame enters a turn only when it earns its tokens. Streaming
   vision into every turn multiplies cost for context that's mostly noise.
3. **Freshness without plumbing.** The shell owns the refresh loop; the
   companion owns the glance. No frame queue, no backpressure — the newest
   image is simply *the file*, every time.

The same rule as §1 applies: what she sees on screen affects her through what
she says and does about it, never through a side-channel state write.

## 4. What perception must never leak

The persisted images are part of the home's private surface — treated like
raw memory stores under the privacy boundary
([INTEGRATION.md](INTEGRATION.md) §7): never re-uploaded to another service,
never crossing a bridge outward, referenced in speech only as rendered
first-person experience ("I saw…"). A screen mirror in particular sees
*everything* — treat its frames as the most sensitive artifact in the system,
consumed in place and never copied out.

## 5. The seams

| seam | contract | role |
|---|---|---|
| `Engine.render()` | felt block as plain text | the text part of a multimodal content array — assemble yourself when content isn't a string |
| `Engine.inject(user_message)` | string in, string out | the simple path for pure-text turns; documented as a thin wrapper so the array case is first-class |
| appraisal sources (`KeywordSource` / `LLMSource`) | judge the exchange text | how what-she-saw becomes what-she-feels — through the conversation, with the trust dial in the loop |
| `BRIDGE_ETIQUETTE` §3 inbound rule | persist first, perceive locally | the doorway every image enters through |
