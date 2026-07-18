"""world.py — the maze's single source of truth (all state, no pixels).

The whole game is this structured state, not a picture:
- for a human skin: any renderer can read it (the terminal view in play.py
  is one; a 3D raycaster consuming the same state is another);
- for the director: it serializes to a compact vector — reading it *is*
  seeing the entire space, no screen needed.

Deliberately minimal: a 2D grid maze + a player + walls + one exit. The
director's verbs: walls (single or by area), the exit (move / open / shut),
or a full interior reshuffle.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

W, H = 15, 15  # grid size


@dataclass
class World:
    # the player
    player: list = field(default_factory=lambda: [1, 1])  # [x, y]
    facing: str = "north"
    # the exit
    exit_pos: list = field(default_factory=lambda: [13, 13])
    exit_open: bool = True
    # walls: list[[x, y]] (kept JSON-friendly)
    walls: list = field(default_factory=list)
    # player-behaviour tracking (fed to the director as "what he's doing")
    steps_taken: int = 0
    stuck_counter: int = 0  # consecutive steps without getting closer to the exit
    last_positions: list = field(default_factory=list)  # recent cells (circling check)
    # what the director last did (shown to her next turn, and to the player log)
    last_change: str = ""
    # outcome
    escaped: bool = False
    turn: int = 0  # how many times the director has intervened

    def __post_init__(self):
        if not self.walls:
            self._build_border()

    def _build_border(self):
        """Border walls only; the interior starts open (the director builds)."""
        w = set()
        for x in range(W):
            w.add((x, 0))
            w.add((x, H - 1))
        for y in range(H):
            w.add((0, y))
            w.add((W - 1, y))
        self.walls = [list(p) for p in sorted(w)]

    # ---------- queries ----------
    def wall_set(self) -> set:
        return {tuple(p) for p in self.walls}

    def is_wall(self, x, y) -> bool:
        return (x, y) in self.wall_set()

    def in_bounds(self, x, y) -> bool:
        return 0 <= x < W and 0 <= y < H

    # ---------- player movement (second-level: the engine's job) ----------
    def move_player(self, dx, dy, facing) -> bool:
        """Try to move. Returns False on a wall or the border."""
        nx, ny = self.player[0] + dx, self.player[1] + dy
        self.facing = facing
        if not self.in_bounds(nx, ny) or self.is_wall(nx, ny):
            return False
        self.last_positions.append(list(self.player))
        self.last_positions = self.last_positions[-12:]
        prev_dist = self._dist_to_exit()
        self.player = [nx, ny]
        self.steps_taken += 1
        if self._dist_to_exit() >= prev_dist:
            self.stuck_counter += 1
        else:
            self.stuck_counter = 0
        if self.player == self.exit_pos and self.exit_open:
            self.escaped = True
        return True

    def _dist_to_exit(self) -> int:
        return abs(self.player[0] - self.exit_pos[0]) + abs(self.player[1] - self.exit_pos[1])

    def is_circling(self) -> bool:
        """Many repeated cells in the last 12 steps = going in circles."""
        if len(self.last_positions) < 8:
            return False
        uniq = {tuple(p) for p in self.last_positions}
        return len(uniq) <= len(self.last_positions) // 2

    # ---------- the director's verbs (she decides; the engine executes) ----
    def add_wall(self, x, y) -> bool:
        """One wall. Never on the player or the exit."""
        if not self.in_bounds(x, y):
            return False
        if [x, y] == self.player or [x, y] == self.exit_pos:
            return False
        if not self.is_wall(x, y):
            self.walls.append([x, y])
            return True
        return False

    def remove_wall(self, x, y) -> bool:
        """Take one wall down (border included — letting him out is allowed)."""
        if (x, y) in self.wall_set():
            self.walls = [w for w in self.walls if w != [x, y]]
            return True
        return False

    def move_exit(self, x, y) -> bool:
        if not self.in_bounds(x, y) or self.is_wall(x, y) or [x, y] == self.player:
            return False
        self.exit_pos = [x, y]
        return True

    def set_exit_open(self, is_open: bool):
        self.exit_open = bool(is_open)

    # ---------- area verbs (one decision should be FELT, not one tile) ----
    def fill_area(self, x1, y1, x2, y2) -> int:
        """Fill a rectangle with wall (seal a corridor / a whole pocket).
        Skips the player and the exit. Returns cells filled."""
        cnt = 0
        for x in range(min(x1, x2), max(x1, x2) + 1):
            for y in range(min(y1, y2), max(y1, y2) + 1):
                if self.add_wall(x, y):
                    cnt += 1
        return cnt

    def clear_area(self, x1, y1, x2, y2) -> int:
        """Tear the interior walls out of a rectangle (open a field / a way
        out). The outer border stays. Returns cells cleared."""
        cnt = 0
        for x in range(min(x1, x2), max(x1, x2) + 1):
            for y in range(min(y1, y2), max(y1, y2) + 1):
                if x in (0, W - 1) or y in (0, H - 1):
                    continue
                if self.remove_wall(x, y):
                    cnt += 1
        return cnt

    def reset_interior(self):
        """Reshuffle the whole floor: every interior wall gone, border kept.
        Space folding — everything he memorized is void."""
        self.walls = []
        self._build_border()

    # ---------- the director's senses ------------------------------------
    def snapshot(self) -> dict:
        """The world as a compact vector. Reading this IS seeing the space."""
        return {
            "grid_size": [W, H],
            "player": {
                "pos": self.player,
                "facing": self.facing,
                "steps_taken": self.steps_taken,
                "stuck_counter": self.stuck_counter,
                "is_circling": self.is_circling(),
                "dist_to_exit": self._dist_to_exit(),
            },
            "exit": {"pos": self.exit_pos, "open": self.exit_open},
            "walls": self.walls,
            "your_last_change": self.last_change,
            "turn": self.turn,
        }

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> World:
        return cls(**json.loads(s))


if __name__ == "__main__":
    w = World()
    print("start: player", w.player, "exit", w.exit_pos, "walls", len(w.walls))
    print("move east:", w.move_player(1, 0, "east"), "->", w.player)
    print("add wall (5,5):", w.add_wall(5, 5))
    print(json.dumps(w.snapshot(), ensure_ascii=False, indent=2))
