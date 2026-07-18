# maze_game — the director shell as a complete, playable game

[`docs/GAME_SHELL.md`](../../docs/GAME_SHELL.md) has a deterministic
transcript (`examples/game_director.py`) proving the loop's *shape*. This
folder is the loop as a **game you actually play**: you run a maze in the
terminal, and a director — the will of the space itself — watches your
world-vector and redecorates around you while you move.

Fully original to this project (designed for the director pattern from the
start), pure standard library, nothing third-party to credit and nothing to
license around — the fully reproducible counterpart to the It Takes Two
ladder next door.

## Run it

```bash
python examples/maze_game/play.py            # zero config: offline stub director
MAZE_LLM=1 python examples/maze_game/play.py # a real model as the space
```

WASD to move, `q` to quit. The LLM path uses any OpenAI-compatible endpoint
via `feltstate.companion.backends_ref.OpenAICompatBackend`:

```bash
export MAZE_LLM=1
export MAZE_LLM_BASE_URL=http://localhost:11434/v1   # ollama, or any compat API
export MAZE_LLM_MODEL=llama3.1
export MAZE_LLM_API_KEY=...                          # if the endpoint needs one
```

The model is prompted with [`HOW_TO_PLAY.md`](HOW_TO_PLAY.md) **verbatim** —
that file is simultaneously the human manual and the system prompt. Give the
backend a character (persona text prepended to the playbook, or a felt block
from a running `Engine`) and the maze acquires a mood.

## What it demonstrates, live

| chapter claim | where you feel it |
|---|---|
| prose senses, not coordinate dumps | `director.scene_summary()` — one constant-length paragraph, raw vector attached for precision |
| minute-scale her, second-scale you | the ask runs on a background thread; you keep walking while she thinks; her changes land turns later |
| big verbs, felt changes | `fill_area` / `clear_area` / `move_exit` / `reset_interior` — one decision seals a corridor, not one tile |
| talking is the control surface | every decision is `{actions, mutter}` in `<<DIRECTOR>>` markers — the taunt and the wall arrive as one act |
| a bad model turn never crashes the world | unparseable replies become a logged no-op; the maze plays on |

## The files

| file | role |
|---|---|
| `world.py` | the single source of truth: grid, walls, exit, player tracking (stuck / circling), the director's verbs, JSON round-trip |
| `director.py` | the game-side layer: prose summary, `<<DIRECTOR>>` extraction, action execution, and two minds — `StubDirector` (offline heuristics) and `LLMDirector` (any `LLMBackend`) |
| `play.py` | the WASD loop: cross-platform key input, terminal render, background asks |
| `HOW_TO_PLAY.md` | the playbook — human manual and LLM system prompt, one file |
