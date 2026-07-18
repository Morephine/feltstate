# itt_bridge — the It Takes Two actuator ladder, as actually played

[`docs/GAME_SHELL.md`](../../docs/GAME_SHELL.md) §7 describes driving a
commercial co-op title with a companion in the second seat. This folder is
that ladder's reference tooling — the (lightly renamed and parametrized)
scripts from the real playthrough, published so the chapter has artifacts,
not just prose.

> **Credit where it's due.** The foundations here are third-party and not
> this project's design: [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) (the
> Lua modding framework), [`vgamepad`](https://pypi.org/project/vgamepad/) on
> the ViGEm bus driver (the emulated X360 pad), and
> [`pymem`](https://pypi.org/project/pymem/) (process-memory access). It
> Takes Two is Hazelight's game. What *is* ours is the composition: the
> tool-daemon shape, the file→cvar command bridge, and the beacon-scan
> telemetry channel described below.

**Honesty first:** everything here is Windows-only, needs the game installed
plus the ViGEm driver, and cannot run in CI — it is a reference for the
pattern, not a maintained product. The engine-side script patch (a few lines
of game-script defining the cvars the bridge talks to) is game-specific and
not included; its contract is documented below.

---

## The ladder, rung by rung

| rung | file | what it does |
|---|---|---|
| 1. virtual gamepad | `pad_daemon.py` | an emulated X360 pad exposed as a tiny local HTTP tool service — the brain composes calls; no script loop owns it |
| — join dance | `join_spam.py`, `hotkey_pad.py` | spam A until player 2 joins; keyboard→pad taps for menus |
| — canned demo | `brain_demo.py` | walk/stop/jump cycle proving the pad moves the character |
| 2. beacon telemetry | inside `pad_daemon.py` (`/state`) | reads the partner's live x/y/z/yaw from process memory — no version-specific offsets (see below) |
| 3. cvar command bridge | `ue4ss_mod/` | a UE4SS Lua mod file-watching `cmd.json` and writing an engine cvar the game's **own** follow capability reads |

## Rung 1 — the pad is a tool daemon, not a loop

```bash
python pad_daemon.py            # GAME_PAD_PORT overrides the port
curl -X POST localhost:8093/button -d '{"btn":"A"}'
curl -X POST localhost:8093/stick  -d '{"side":"left","x":0,"y":1}'
curl -X POST localhost:8093/spam   -d '{"btn":"A","count":20}'
curl localhost:8093/state
```

Endpoints: `GET /status /state` · `POST /button /button_down /button_up
/stick /spam /screenshot /quit`. Two details earned by play:

* **sticks are re-asserted every 50 ms** by a background worker — ViGEm
  lets an un-refreshed stick decay back to center;
* the daemon holds no policy. Deciding *when* to press what is the
  companion's turn-by-turn business, which is the whole point
  (`docs/GAME_SHELL.md` §7: the brain holds the tools, the loop doesn't
  hold the brain).

## Rung 2 — beacon-scan telemetry (no offsets to rot)

Hardcoded memory offsets die with every game patch. This channel avoids them
entirely:

1. the engine-side script writes each telemetry value into a cvar as
   `beacon + value` — e.g. partner-x lands near 7,000,000, y near 8,000,000,
   z near 9,000,000, yaw near 10,000,000;
2. `pad_daemon.py` scans the process's committed R/W memory for float32s
   near each beacon, snapshots twice, and keeps the candidates that *change*
   (live values move; stale copies don't — the yaw beacon uses a tighter
   tolerance and a stability check instead), preferring adjacent pairs;
3. `/state` then returns `read(addr) − beacon` per axis, live, in
   milliseconds.

The beacons make the values findable *by magnitude* in any build — the scan
re-locks after a patch or a restart with zero maintenance. That is the whole
trick, and it is the part most worth stealing.

## Rung 3 — the cvar bridge: borrow the game's own legs

`ue4ss_mod/Scripts/main.lua` polls a `cmd.json` (path constant at the top —
edit it) every 100 ms and, on change, issues `Mod.FollowOther <0|1>` as a
console command on the player controller. The engine-side contract: define
that cvar in the game's script layer and let an **existing** capability read
it — in our playthrough, the game's own follow-the-other-player behaviour.
No synthetic inputs at all on this rung: the mod flips a switch, and the
game walks its character with its native movement code.

Install (UE4SS v3.0.1+): unzip UE4SS into the game's `Binaries/Win64/`, copy
`ue4ss_mod/` into `ue4ss/Mods/<name>/`, add `<name> : 1` to `mods.txt`,
start the game, and look for the mod's load line in the UE4SS console.

## Run order, as played

1. start the game to the lobby; run `join_spam.py`; walk to the join screen —
   player 2 joins itself; Ctrl-C the spammer;
2. `hotkey_pad.py` for any menus (F6=A, F7=B, D-pad on F8–F11);
3. `pad_daemon.py` for the real session — the companion curls it
   turn by turn (or `brain_demo.py` for the canned proof);
4. the UE4SS mod carries the "follow me" switch whenever walking should be
   the game's job instead of stick micro-management.
