"""director.py — the game-specific layer between the maze and a director mind.

Three jobs, mirroring the contract in docs/GAME_SHELL.md:

1. summarize the world *as prose* (plus the raw vector for precision) — the
   director reads a situation, not a coordinate dump;
2. carry the decision format: ``{"actions": [...], "mutter": "..."}`` inside
   ``<<DIRECTOR>> ... <<DIRECTOR_END>>`` markers (brace-scrape fallback);
3. execute her actions against the world — how ops map to the maze is this
   game's business, never the director's.

Two director minds ship:

* :class:`StubDirector` — offline heuristics, zero config, so the game is
  playable out of the box;
* :class:`LLMDirector` — any :class:`feltstate.companion.backend.LLMBackend`
  (e.g. ``OpenAICompatBackend``) prompted with HOW_TO_PLAY.md verbatim; the
  model answers as the space itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from world import World

HOWTO_PATH = Path(__file__).resolve().parent / "HOW_TO_PLAY.md"


# --------------------------------------------------------------------------- #
# Senses: prose first, vector attached                                        #
# --------------------------------------------------------------------------- #
def scene_summary(world: World) -> str:
    v = world.snapshot()
    p = v["player"]
    return (
        f"the player is at {p['pos']} facing {p['facing']}, {p['steps_taken']} steps in, "
        f"stuck for {p['stuck_counter']}{', going in circles' if p['is_circling'] else ''}, "
        f"{p['dist_to_exit']} from the exit. exit at {v['exit']['pos']} "
        f"({'open' if v['exit']['open'] else 'shut'}). "
        f"your last change: {v['your_last_change'] or 'nothing yet'}.\n"
        f"[full snapshot when you need exact coordinates: {json.dumps(v)}]"
    )


# --------------------------------------------------------------------------- #
# The decision format                                                          #
# --------------------------------------------------------------------------- #
def extract_decision(text: str) -> dict | None:
    """Pull the decision JSON out of a reply: markers first, braces fallback."""
    s_tag, e_tag = "<<DIRECTOR>>", "<<DIRECTOR_END>>"
    si, ei = text.find(s_tag), text.find(e_tag)
    blob = text[si + len(s_tag) : ei].strip() if (si != -1 and ei != -1) else text
    try:
        return json.loads(blob)
    except (ValueError, TypeError):
        try:
            a = blob.index("{")
            b = blob.rindex("}") + 1
            return json.loads(blob[a:b])
        except (ValueError, TypeError):
            return None


# --------------------------------------------------------------------------- #
# Execution: her intent, the game's hands                                     #
# --------------------------------------------------------------------------- #
def apply_actions(world: World, decision: dict) -> list[str]:
    log: list[str] = []
    for act in (decision or {}).get("actions", [])[:3]:
        try:
            op = act.get("op", "")
            if op == "add_wall":
                ok = world.add_wall(int(act["x"]), int(act["y"]))
                log.append(f"wall at ({act['x']},{act['y']})" if ok else "(wall refused)")
            elif op == "remove_wall":
                ok = world.remove_wall(int(act["x"]), int(act["y"]))
                log.append(f"wall down at ({act['x']},{act['y']})" if ok else "(no wall there)")
            elif op == "move_exit":
                ok = world.move_exit(int(act["x"]), int(act["y"]))
                log.append(
                    f"exit moved to ({act['x']},{act['y']})" if ok else "(exit move refused)"
                )
            elif op == "set_exit":
                world.set_exit_open(bool(act.get("open", True)))
                log.append("exit opened" if world.exit_open else "exit shut")
            elif op == "fill_area":
                n = world.fill_area(int(act["x1"]), int(act["y1"]), int(act["x2"]), int(act["y2"]))
                log.append(f"sealed {n} cells")
            elif op == "clear_area":
                n = world.clear_area(int(act["x1"]), int(act["y1"]), int(act["x2"]), int(act["y2"]))
                log.append(f"opened {n} cells")
            elif op == "reset_interior":
                world.reset_interior()
                log.append("the whole floor reshuffled")
        except (KeyError, ValueError, TypeError):
            log.append(f"(bad action: {act})")
    if log:
        world.last_change = "; ".join(log)
    world.turn += 1
    return log


# --------------------------------------------------------------------------- #
# Director minds                                                               #
# --------------------------------------------------------------------------- #
class StubDirector:
    """Offline heuristics with a temper — so the game plays with zero config.

    Deterministic given the same walk: keyed off the world's own state, no
    randomness. It reacts the way the playbook suggests: presses early,
    teases the middle, relents when he's earned it.
    """

    name = "stub"

    def decide(self, world: World) -> dict:
        v = world.snapshot()
        p = v["player"]
        px, py = p["pos"]
        ex, ey = v["exit"]["pos"]
        turn = world.turn

        if turn == 0:  # opening move: a diagonal spine between him and the exit
            return {
                "actions": [{"op": "fill_area", "x1": 7, "y1": 1, "x2": 7, "y2": 10}],
                "mutter": "Walls grow here. Did nobody tell you?",
            }
        if p["is_circling"] or p["stuck_counter"] >= 6:  # mercy, eventually
            return {
                "actions": [
                    {
                        "op": "clear_area",
                        "x1": min(px, ex),
                        "y1": min(py, ey),
                        "x2": max(px, ex),
                        "y2": max(py, ey),
                    }
                ],
                "mutter": "Fine. A straight answer, just this once.",
            }
        if p["dist_to_exit"] <= 4 and turn < 4:  # nearly there? not yet.
            return {
                "actions": [{"op": "move_exit", "x": (ex + 6) % 13 + 1, "y": (ey + 9) % 13 + 1}],
                "mutter": "The exit is wherever I say it is.",
            }
        if turn % 3 == 2:  # a pinch behind him, just to be felt
            bx = max(1, min(13, px - 1))
            return {
                "actions": [
                    {
                        "op": "fill_area",
                        "x1": bx,
                        "y1": max(1, py - 1),
                        "x2": bx,
                        "y2": min(13, py + 1),
                    }
                ],
                "mutter": "The way back is gone, by the way.",
            }
        return {"actions": [], "mutter": ""}  # letting him stew is a move too

    def farewell(self, world: World) -> str:
        return f"Out in {world.steps_taken} steps. I let you have that one."


class LLMDirector:
    """Any LLMBackend, prompted with the playbook — the model IS the space.

    ``backend.complete(messages)`` in, ``{actions, mutter}`` out. A reply the
    extractor can't parse becomes a safe no-op carrying ``_error``, so a bad
    model turn never crashes the maze.
    """

    name = "llm"

    def __init__(self, backend, howto_text: str | None = None) -> None:
        self.backend = backend
        self.howto = howto_text or HOWTO_PATH.read_text(encoding="utf-8")

    def _messages(self, prompt: str) -> list[dict]:
        return [
            {"role": "system", "content": self.howto},
            {"role": "user", "content": prompt},
        ]

    def decide(self, world: World) -> dict:
        prompt = (
            scene_summary(world) + "\n\nYour turn. Reply in the output format from the playbook."
        )
        try:
            reply = self.backend.complete(self._messages(prompt))
        except Exception as e:  # noqa: BLE001 - a dead backend must not kill the game
            return {"actions": [], "mutter": "", "_error": f"backend: {e}"}
        decision = extract_decision(reply or "")
        if decision is None:
            return {"actions": [], "mutter": "", "_error": "unparseable reply"}
        return decision

    def farewell(self, world: World) -> str:
        try:
            reply = self.backend.complete(
                self._messages(
                    f"the game is over — he escaped your maze in {world.steps_taken} steps; "
                    "you didn't hold him. One closing line to him, plain text, no JSON."
                )
            )
            return (reply or "").strip().splitlines()[0] if reply else ""
        except Exception:  # noqa: BLE001
            return ""
