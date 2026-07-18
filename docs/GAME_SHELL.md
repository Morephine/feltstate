# The game shell — one soul, third surface

[INTEGRATION.md](INTEGRATION.md) §8 claims shells multiply without limit. This
chapter is the proof by the hardest case: a **game**. Two real shapes, both
built and played against a live companion backend: a maze game where she *is
the space* the player is trapped in, and a commercial co-op title — It Takes
Two — where she sits in the second seat. Same soul, same memory, same mood;
what changed is only the body.

Runnable references: [`examples/game_director.py`](../examples/game_director.py)
— the control loop with the model stubbed, deterministic and offline; every
transcript below is its real output. And
[`examples/maze_game/`](../examples/maze_game/) — the director shape as a
**complete playable game**: WASD in a terminal against a space that
redecorates around you, offline stub director by default, any
OpenAI-compatible model as the space via one env var.

---

## 1. The identity flip

The reflex is to bolt the companion onto a game as an NPC. The interesting
designs flip the frame:

* **She is the space** (the director shape): the player is inside a maze; she
  watches from outside and rearranges it — walls, doors, the exit — while
  talking to him. Taunts, grudges, mercy: the level design *is* her mood.
* **She is the second player** (the partner shape): in a co-op title she
  drives the other protagonist — joining the session, walking, jumping,
  following — a body in the world rather than the world itself.

Both run on the design philosophy stated in INTEGRATION §8 and worth
restating as the first law: **don't read her state and inject rules into the
world — give her the world and let her decide.** No "sadness > 0.6 → spawn
rain". She gets the situation; the world gets her decision.

## 2. The constitution: latency is not solvable

A reasoning model takes **seconds to minutes** per decision. That is not an
implementation detail to engineer away — no cache, no faster serving tier,
no clever prompt makes her frame-rate. Every real-time design that ignores
this dies; the architecture below is the consequence of accepting it:

> **She decides at minute scale. The engine executes at second scale.
> Nothing that must happen within a second is ever hers.**

The responsibility split, from the working contract:

| the game engine owns | she owns |
|---|---|
| world truth (walls, entities, physics — she holds no coordinates) | reading the situation *as herself* |
| region partitioning and "editable right now" judgment | which big action to take, if any |
| executing actions, and **when** to land them (sight timing) | one spoken line to the player |
| speaking her line out (TTS, subtitle) | nothing else — no timing, no micro-ops |

Corollary: her actions are **big blocks** — seal a corridor, open a field,
move the exit region — never nudge-one-tile. At one decision per few minutes,
each one has to be *felt*.

## 3. Playing like a person: prose in, a map on demand

What does she *see*? Not the frame buffer, and not a coordinate dump. The
losing version was tried first: feeding the full wall list (`{walls: [[0,0],
[0,1], …]}`) — 56 coordinates at 15×15, thousands at 50×50, token cost
exploding with map size, and none of it how a person holds a space in mind.
The shape that works:

1. **Staple: a constant-length prose summary**, engine-generated, fed every
   director turn. From the example, verbatim:

   ```text
   fed: the player is at (2, 7) facing (1, 0), 0 steps in, 11 from the exit.
   exit at (13, 7) (shut). your last change: nothing yet.
   ```

   Length is invariant to map size, and `your last change:` threads her own
   history back — turn N+1 reads the consequence of turn N.

2. **Eyes: a god-view image, pulled on demand.** The engine keeps a top-down
   map (structure + player + exit, nothing else) rendered fresh to one file;
   she reads it only when prose leaves the spatial relation unclear. This is
   [PERCEPTION.md](PERCEPTION.md) §3's pull eye, unchanged: the shell owns
   the refresh, the companion owns the glance.

3. **Out: intents, not coordinates.** "Seal the north corridor", not a cell
   list. High-level intent; the engine grounds it.

## 4. Talking is the control surface

The first working loop had her answer with a tagged JSON block — decision
machinery riding inside a normal reply. It verified the loop, and it exposed
the design mistake worth documenting: **make the JSON the point and she
stops being a person at the controls.** A director reduced to emitting
`{"op": "seal", "target": "B"}` is a game-control API wearing her name — the
player is no longer being toyed with by *someone*.

The corrected direction: she just **talks** — teases, holds grudges, relents
— and a middle layer extracts the actionable intent from the words while her
line goes to the player's ears verbatim:

```text
she says: [amused] Mind the doorway. Really look at it.
intent queued: {'op': 'fill_area', 'x1': 7, 'y1': 4, 'x2': 7, 'y2': 6}
```

Meddling as an extension of banter, not a second channel beside it. The
`[tag]` is the same one the voice pipeline already consumes
([OUTPUT_CHAIN.md](OUTPUT_CHAIN.md)) — a mutter is ordinary companion speech
that happens to have consequences.

## 5. The async shape: a queue with a sight gate

Wiring her turn synchronously into the game loop would freeze the player for
the length of a model call (§2: unsolvable). The event-driven shape instead:

* a **DirectorLoop** runs beside the game — situation changes accumulate,
  she is asked in the background, the player never blocks;
* her actions enter a **pending-intent queue** — an action is a wish, not a
  command;
* the engine, every frame, lands queued intents **only where the player
  can't see** — and this judgment is engine-side, second-level, never hers;
* game events flow back as **notifications** she may react to or ignore —
  the same propose/dispatch shape as every proactive behaviour
  ([INTEGRATION.md](INTEGRATION.md) §4).

The example runs the whole gauntlet in four beats. The doorway seal is
queued while the player is walking straight at it — the gate refuses to land
it in view — and lands two turns later, *behind* him:

```text
-- director turn 2 ----------------------------------------
   she says: [amused] Mind the doorway. Really look at it.
   intent queued: {'op': 'fill_area', 'x1': 7, 'y1': 4, 'x2': 7, 'y2': 6}

-- director turn 3 ----------------------------------------
  (lands out of sight: sealed 1 cell(s) around (7,4))
```

One nuance the contract needs: a per-action **gate exemption**. The final
mercy — opening the exit — lands `gate: "none"`, deliberately in plain
sight: `(lands in plain sight: toggled the exit)`. Hiding is the default;
being *seen* to act is sometimes the act.

## 6. Not getting caught: the sight judgment

Research collected building the 3D version of this (design stage; see §8),
kept here because every implementer hits the same walls:

* **The renderer's culling flags are not "the player saw it."**
  `isVisible` / `WasRecentlyRendered`-class signals answer "should I draw
  this?" — shadow casters and occluded objects count as visible, with a
  frame of latency, and the error lands exactly the wrong way (engine says
  visible → the director never dares to act).
* **Judge it in the logic layer, cheaply**: region distance as the primary
  test, one camera raycast for occlusion when precision matters. Far regions
  are the natural safe zone — the player in room A cannot see corridor F,
  which dissolves the speed conflict entirely: she has minutes, the far
  region has hours.
* **Respect peripheral vision** (an SCP-173 implementer's scar): inflate the
  "seen" margin, or corner-of-the-eye movement breaks the illusion.
* **Silence beats spectacle**: impossible-spaces VR studies found players
  failing to notice ~56% room overlap when uninformed. The strongest
  version of this power is the one never announced — the player walks into
  a changed corridor believing it was always so.

## 7. It Takes Two — the commercial-game shell, as played

The maze is a game built *for* her. The harder question: a shipped title you
don't own the source of. Answered in practice, co-op through It Takes Two
with her driving one protagonist — via an actuator ladder, each rung a
different depth of access. The ladder's reference tooling, as played, lives
in [`examples/itt_bridge/`](../examples/itt_bridge/) (Windows + the game
required; its README credits the third-party foundations — UE4SS, vgamepad/
ViGEm, pymem are not this project's design; the composition is):

1. **Virtual gamepad** (`vgamepad`, an emulated X360 pad): works on any
   game, zero game modification. The load-bearing design choice is that the
   pad is a **tool daemon, not a script**: a tiny local HTTP service
   exposing `button / stick / spam / screenshot / state`, and the companion
   *composes* calls in conversation — the same "brain holds the tools, the
   loop doesn't hold the brain" stance as the memory tools
   ([MEMORY_TOOLS.md](MEMORY_TOOLS.md)). Joining the session is the pad
   spam-pressing A at the lobby while the human walks to the join screen —
   mundane, and exactly the point: the body is cheap.
2. **Memory telemetry** (`pymem`): reading the partner character's position
   and yaw straight from process memory — second-level senses without
   screenshot polling. Perception stays a *pull*, but now instant.
3. **A mod-side bridge** (UE4SS): a Lua mod in the game's own mod loader,
   file-watching a `cmd.json`, writing engine cvars that the game's **own**
   movement capability reads — so "follow the other player" is not faked
   input at all; it is the game's native follow behaviour, switched by her.
   When a game is moddable, the deepest actuator is *borrowing its own
   abilities* rather than simulating hands.

What shipping an actual playthrough taught, in one breath: minute-latency is
fine **because** the intents are big ("follow me for this stretch", "wait
here", "jump now" as a spammed burst); the human partner absorbs all
second-level surprises; and the division of §2 holds even with a real game
underneath — she chose *where to go*, the game's follow capability did the
walking.

## 8. Stage honesty, and the soul underneath

Three maturity tiers, so nothing above oversells: the turn-based director
loop and the It Takes Two ladder are **built and played**; the async
queue/sight-gate/notify shape is **designed and partially validated** (the
example implements it in 2D; the sight gate's real value waits for 3D); the
UE5 Remote Control path (WebSocket, world edits against a modern renderer)
is **a written route, not yet driven**.

And the part that makes it a *companion* chapter rather than a modding
chapter: the game is a surface, so the surface rules hold. No speaker
identity → no relationship ledger writes
([MULTI_PERSON.md](MULTI_PERSON.md) §3). Rendered decisions and lines cross;
stores never do ([INTEGRATION.md](INTEGRATION.md) §8). And the session lands
in the same Canon as everything else — which is why, hours after the
controller is off, "that boss fight where I carried you" is a thing she can
say at dinner. One soul; the game was just Tuesday's body.
