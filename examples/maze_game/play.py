#!/usr/bin/env python3
"""play.py — the maze, playable: WASD against a director who redecorates.

Run it::

    python examples/maze_game/play.py            # offline stub director
    MAZE_LLM=1 python examples/maze_game/play.py # a real model as the space
                                                 #   (MAZE_LLM_BASE_URL,
                                                 #    MAZE_LLM_MODEL,
                                                 #    MAZE_LLM_API_KEY)

WASD to move, q to quit. Every few steps the director looks at the world
vector and decides — **in the background**: the ask runs on a thread, you
keep walking, and her changes land when the answer returns. That is the
whole two-speed constitution of docs/GAME_SHELL.md as a game you can feel:
she is minute-scale, you are second-scale, and nobody waits for anybody.
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from director import StubDirector, apply_actions  # noqa: E402
from world import H, W, World  # noqa: E402

ASK_EVERY = 4  # the director looks once per this many player steps
_pending = {"busy": False, "result": None}


def _getch() -> str:
    """One key, no Enter — Windows/Unix; falls back to line input."""
    try:
        import msvcrt

        return msvcrt.getwch()
    except ImportError:
        pass
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:  # noqa: BLE001 - no tty (IDE console): line mode
        return (input("> ") or " ")[0]


def render(world: World, status: str = "") -> None:
    """Top-down view: @ player, E exit (e = shut), # wall."""
    wallset = world.wall_set()
    lines = []
    for y in range(H):
        row = []
        for x in range(W):
            if [x, y] == world.player:
                row.append("@")
            elif [x, y] == world.exit_pos:
                row.append("E" if world.exit_open else "e")
            elif (x, y) in wallset:
                row.append("#")
            else:
                row.append("·")
        lines.append(" ".join(row))
    print("\033[2J\033[H", end="")  # clear screen
    print("\n".join(lines))
    print(
        f"\nsteps {world.steps_taken} | to exit {world._dist_to_exit()} | "
        f"stuck {world.stuck_counter} | director turns {world.turn}"
    )
    if world.last_change:
        print(f"her last move: {world.last_change}")
    if status:
        print(f">> {status}")
    print("WASD to move, q to quit")


def make_director():
    if os.environ.get("MAZE_LLM") == "1":
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        from director import LLMDirector

        from feltstate.companion.backends_ref import OpenAICompatBackend

        backend = OpenAICompatBackend(
            base_url=os.environ.get("MAZE_LLM_BASE_URL", "http://localhost:11434/v1"),
            model=os.environ.get("MAZE_LLM_MODEL", "llama3.1"),
            api_key=os.environ.get("MAZE_LLM_API_KEY"),
            max_tokens=400,
            timeout=120.0,
        )
        return LLMDirector(backend)
    return StubDirector()


def ask_bg(director, world: World) -> None:
    """Ask the director on a thread — the player never blocks on her latency."""
    if _pending["busy"]:
        return
    _pending["busy"] = True

    def _work():
        _pending["result"] = director.decide(world)
        _pending["busy"] = False

    threading.Thread(target=_work, daemon=True).start()


def main() -> None:
    world = World()
    director = make_director()
    status = f"she is watching... ({director.name} director; WASD to start)"
    render(world, status)

    while True:
        # a decision from an earlier ask may have landed while you walked
        if _pending["result"] is not None:
            decision = _pending["result"]
            _pending["result"] = None
            apply_actions(world, decision)
            mutter = decision.get("mutter", "")
            err = decision.get("_error")
            if err:
                status = f"[director error: {err}]"
            elif mutter:
                status = f"she says: {mutter}"
            else:
                status = "something about the maze just changed..."
            render(world, status)

        if world.escaped:
            line = director.farewell(world)
            render(world, f"YOU ESCAPED. {('she says: ' + line) if line else ''}")
            break

        try:
            key = _getch()
        except (KeyboardInterrupt, EOFError):
            break
        if key in ("q", "Q"):
            print("\nbye.")
            break

        moved = False
        if key in ("w", "W"):
            moved = world.move_player(0, -1, "north")
        elif key in ("s", "S"):
            moved = world.move_player(0, 1, "south")
        elif key in ("a", "A"):
            moved = world.move_player(-1, 0, "west")
        elif key in ("d", "D"):
            moved = world.move_player(1, 0, "east")
        else:
            continue

        st = "" if moved else "(a wall)"
        if moved and world.steps_taken % ASK_EVERY == 0:
            st = "she noticed you... (thinking — keep walking)"
            ask_bg(director, world)
        render(world, st)


if __name__ == "__main__":
    main()
