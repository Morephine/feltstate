#!/usr/bin/env python3
"""game_director — the game shell's control loop, runnable on a table top.

Run it::

    python examples/game_director.py

A game is the companion's third kind of surface (after the desktop shell and
the chat bridge, `docs/INTEGRATION.md` §8): a world is summarized *to* her,
she answers **as herself** with a decision — a few big actions and one spoken
line — and the engine lands those actions when it is safe. This example is
that loop with every network piece replaced by a deterministic stub, so the
*shape* is fully visible:

* a 15×15 maze world with a scripted greedy player trying to reach the exit;
* a **director** fed a constant-length prose summary (never a coordinate
  dump) who returns ``{"actions": [...], "mutter": "..."}``;
* an **intent queue with a sight gate**: actions never execute immediately —
  they land only when the player can't see the affected cells (facing away,
  or a wall in between);
* the mutter and the meddling arriving as one act — she talks, and the space
  answers.

The stub director answers instantly; a real one does not. In production the
director is a full reasoning model with **seconds-to-minutes latency, and
that latency is not solvable** — it is the physical premise the whole design
is built around (minute-scale intents, an async loop, a landing queue),
never a bug the next optimization will remove. See `docs/GAME_SHELL.md`.

Deterministic: no randomness, no wall clock — two runs are byte-identical.
"""

from __future__ import annotations

import sys
from collections import deque

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

W, H = 15, 15
ASK_EVERY = 4  # the player moves this many steps between director turns


# --------------------------------------------------------------------------- #
# The world — walls, an exit, one player. The engine side owns all of this.   #
# --------------------------------------------------------------------------- #
class World:
    def __init__(self) -> None:
        self.walls: set[tuple[int, int]] = set()
        for x in range(W):
            self.walls |= {(x, 0), (x, H - 1)}
        for y in range(H):
            self.walls |= {(0, y), (W - 1, y)}
        # a spine of interior walls with two gaps
        for y in range(2, 13):
            if y not in (5, 10):
                self.walls.add((7, y))
        self.player = (2, 7)
        self.facing = (1, 0)  # unit step of the last move
        self.exit = (13, 7)
        self.exit_open = False
        self.steps = 0
        self.trail: deque[tuple[int, int]] = deque(maxlen=8)
        self.last_change = "nothing yet"

    # -- player physics (second-level: always the engine's job) -------------
    def is_wall(self, c: tuple[int, int]) -> bool:
        return c in self.walls

    def bfs_step(self) -> tuple[int, int] | None:
        """The scripted player: one greedy BFS step toward the exit."""
        start, goal = self.player, self.exit
        prev: dict[tuple[int, int], tuple[int, int]] = {start: start}
        q = deque([start])
        while q:
            cur = q.popleft()
            if cur == goal:
                break
            x, y = cur
            for nxt in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):
                if nxt not in prev and not self.is_wall(nxt):
                    prev[nxt] = cur
                    q.append(nxt)
        if goal not in prev:
            return None  # sealed in — the player waits
        node = goal
        while prev[node] != start:
            node = prev[node]
        return node

    def step_player(self) -> None:
        nxt = self.bfs_step()
        if nxt is None:
            self.steps += 1
            return
        self.facing = (nxt[0] - self.player[0], nxt[1] - self.player[1])
        self.player = nxt
        self.trail.append(nxt)
        self.steps += 1

    @property
    def dist_to_exit(self) -> int:
        return abs(self.player[0] - self.exit[0]) + abs(self.player[1] - self.exit[1])

    @property
    def is_circling(self) -> bool:
        return len(self.trail) == self.trail.maxlen and len(set(self.trail)) <= 4

    # -- the director's operations (big blocks, never single cells) ---------
    def fill_area(self, x1: int, y1: int, x2: int, y2: int) -> int:
        n = 0
        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                c = (x, y)
                if c not in self.walls and c != self.player and c != self.exit:
                    self.walls.add(c)
                    n += 1
        return n

    def clear_area(self, x1: int, y1: int, x2: int, y2: int) -> int:
        n = 0
        for x in range(max(1, x1), min(W - 1, x2) + 1):
            for y in range(max(1, y1), min(H - 1, y2) + 1):
                if (x, y) in self.walls:
                    self.walls.discard((x, y))
                    n += 1
        return n

    def move_exit(self, x: int, y: int) -> None:
        self.walls.discard((x, y))
        self.exit = (x, y)

    # -- rendering (the god view a skin could draw as a PNG) -----------------
    def render(self) -> str:
        rows = []
        for y in range(H):
            row = []
            for x in range(W):
                c = (x, y)
                if c == self.player:
                    row.append("@")
                elif c == self.exit:
                    row.append("E" if self.exit_open else "e")
                else:
                    row.append("#" if c in self.walls else "·")
            rows.append(" ".join(row))
        return "\n".join(rows)


# --------------------------------------------------------------------------- #
# The sight gate — actions are INTENTS; the engine lands them out of sight.   #
# --------------------------------------------------------------------------- #
def _blocked(world: World, a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Wall between a and b along a Bresenham line? (endpoints excluded)"""
    x0, y0 = a
    x1, y1 = b
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while (x0, y0) != (x1, y1):
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
        if (x0, y0) != (x1, y1) and world.is_wall((x0, y0)):
            return True
    return False


def _cell_unseen(world: World, cell: tuple[int, int]) -> bool:
    """2D stand-in for "the player can't see this": behind his facing, or
    walled off. (In a 3D engine this judgment is a distance check plus a
    raycast — and never the renderer's culling flags; see the chapter.)"""
    px, py = world.player
    fx, fy = world.facing
    rel = (cell[0] - px, cell[1] - py)
    behind = (rel[0] * fx + rel[1] * fy) < 0
    return behind or _blocked(world, world.player, cell)


def _action_cells(world: World, act: dict) -> list[tuple[int, int]]:
    op = act["op"]
    if op in ("fill_area", "clear_area"):
        return [
            (x, y) for x in range(act["x1"], act["x2"] + 1) for y in range(act["y1"], act["y2"] + 1)
        ]
    if op == "move_exit":
        return [world.exit, (act["x"], act["y"])]
    return [world.exit]  # set_exit_open


def land_safe_intents(world: World, queue: list[dict]) -> None:
    """Engine-side, every step: land each queued intent whose whole footprint
    is out of the player's sight. Second-level timing is the engine's job —
    the director never holds it."""
    for act in list(queue):
        exempt = act.get("gate") == "none"  # a change she WANTS seen (rare)
        if exempt or all(_cell_unseen(world, c) for c in _action_cells(world, act)):
            op = act["op"]
            if op == "fill_area":
                n = world.fill_area(act["x1"], act["y1"], act["x2"], act["y2"])
                world.last_change = f"sealed {n} cell(s) around ({act['x1']},{act['y1']})"
            elif op == "clear_area":
                n = world.clear_area(act["x1"], act["y1"], act["x2"], act["y2"])
                world.last_change = f"opened {n} cell(s) around ({act['x1']},{act['y1']})"
            elif op == "move_exit":
                world.move_exit(act["x"], act["y"])
                world.last_change = f"moved the exit to ({act['x']},{act['y']})"
            elif op == "set_exit_open":
                world.exit_open = act["open"]
                world.last_change = "toggled the exit"
            queue.remove(act)
            where = "in plain sight" if exempt else "out of sight"
            print(f"  (lands {where}: {world.last_change})")


# --------------------------------------------------------------------------- #
# The director's senses — constant-length prose, never a coordinate dump.     #
# --------------------------------------------------------------------------- #
def scene_summary(world: World) -> str:
    return (
        f"the player is at {world.player} facing {world.facing}, "
        f"{world.steps} steps in, {world.dist_to_exit} from the exit"
        f"{', going in circles' if world.is_circling else ''}. "
        f"exit at {world.exit} ({'open' if world.exit_open else 'shut'}). "
        f"your last change: {world.last_change}."
    )


# --------------------------------------------------------------------------- #
# The stub director — same contract as the real one: summary in,             #
# {"actions": [...], "mutter": "..."} out. Instant here; NOT instant in life. #
# --------------------------------------------------------------------------- #
def stub_director(summary: str, turn: int) -> dict:
    if turn == 1:  # opening move: the exit was never where he thinks it is
        return {
            "actions": [{"op": "move_exit", "x": 13, "y": 2}],
            "mutter": "[smirk] The exit's wherever I say it is. Off you go.",
        }
    if turn == 2:  # seal the doorway — it will only land once he's through it
        return {
            "actions": [{"op": "fill_area", "x1": 7, "y1": 4, "x2": 7, "y2": 6}],
            "mutter": "[amused] Mind the doorway. Really look at it.",
        }
    if turn == 3:  # open the wrong door behind him, just to be confusing
        return {
            "actions": [{"op": "clear_area", "x1": 7, "y1": 9, "x2": 7, "y2": 11}],
            "mutter": "[smirk] I open doors too. Wrong ones, mostly.",
        }
    if turn == 4:  # relent — and make sure he SEES the mercy (gate-exempt)
        return {
            "actions": [{"op": "set_exit_open", "open": True, "gate": "none"}],
            "mutter": "[tender] Go on then. You earned this one.",
        }
    return {"actions": [], "mutter": ""}


# --------------------------------------------------------------------------- #
# The loop — player at second-level cadence, director at minute-level.        #
# --------------------------------------------------------------------------- #
def main() -> None:
    world = World()
    queue: list[dict] = []
    turn = 0

    print("=" * 64)
    print("game_director — intents, a sight gate, and one running mouth")
    print("=" * 64)
    print(world.render())

    for _ in range(60):
        if world.steps % ASK_EVERY == 0:
            turn += 1
            summary = scene_summary(world)
            print(f"\n-- director turn {turn} " + "-" * 40)
            print(f"   fed: {summary}")
            decision = stub_director(summary, turn)
            if not decision["mutter"] and not decision["actions"]:
                pass  # she has nothing this turn — silence is allowed
            else:
                print(f"   she says: {decision['mutter']}")
            for act in decision["actions"]:
                print(f"   intent queued: {act}")
            queue.extend(decision["actions"])

        land_safe_intents(world, queue)
        world.step_player()

        if world.player == world.exit and world.exit_open:
            print(f"\nthe player reaches the exit in {world.steps} steps.")
            break

    print()
    print(world.render())
    print("\nqueue drained." if not queue else f"\nstill pending: {queue}")
    print("The director spoke four times and touched nothing directly -")
    print("every change landed out of sight, on the engine's clock.")


if __name__ == "__main__":
    main()
