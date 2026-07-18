#!/usr/bin/env python3
"""memory_tools — Canon exposed as function-calling tools, end to end.

Run it::

    python examples/memory_tools.py

The pattern: instead of stuffing memories into every prompt, give the reply
model **five tools** and let it decide when to dig. This script shows the whole
plumbing an app needs —

* the OpenAI-style JSON schemas for ``remember / recall / correct / retract /
  history`` (copy them into your tool list as-is);
* the dispatcher that maps a tool call onto a real on-disk :class:`Canon`;
* a full four-turn trace: store a fact, recall it, *correct* it, then ask
  "what did I used to think?" — the bi-temporal answer other memory layers
  can't give.

The tool calls, store, salience numbers, and lifecycle are all real. Only the
"model" is scripted (this example stays zero-network); wire the same schemas
into any function-calling LLM and delete the script.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from feltstate.memory.canon import Canon

# --------------------------------------------------------------------------- #
# 1. The tool schemas — hand these to any function-calling model.             #
# --------------------------------------------------------------------------- #
MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Store one durable fact worth keeping across sessions. Use for "
                "things the user tells you about themselves or their world."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "the fact, one plain sentence"},
                    "why": {"type": "string", "description": "optional: why it matters"},
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Search memory for facts matching a keyword. Recalling a fact "
                "strengthens it (used memory sticks)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correct",
            "description": (
                "Supersede a remembered fact with a corrected version. The old "
                "belief is kept, auditable, marked superseded — never erased."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "keyword or id of the fact to correct",
                    },
                    "corrected_fact": {"type": "string"},
                },
                "required": ["target", "corrected_fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retract",
            "description": "Mark a fact retracted (hidden from recall, kept for audit).",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "history",
            "description": (
                "The audit trail for a fact: every version ever held, each with "
                "its validity window and status (active / superseded / retracted). "
                "Answers 'what did I used to think?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# 2. The dispatcher — the only glue an app writes.                            #
# --------------------------------------------------------------------------- #
def execute_memory_tool(canon: Canon, name: str, args: dict) -> dict:
    """Map one tool call onto the store. Returns a JSON-safe result dict."""
    if name == "remember":
        row = canon.add("user", args["fact"], action="told me", why=args.get("why", ""))
        return {"kept": row["object"], "id": row["id"]}
    if name == "recall":
        hits = canon.search(args["query"])[:3]
        return {
            "hits": [{"fact": h["object"], "salience": h["intensity"], "id": h["id"]} for h in hits]
        }
    if name == "correct":
        row = canon.correct(args["target"], object=args["corrected_fact"])
        if not row:
            return {"error": "no active fact matched"}
        return {"corrected_to": row["object"], "old_version_kept": True}
    if name == "retract":
        row = canon.retract(args["target"])
        return {"retracted": row.get("object")} if row else {"error": "no active fact matched"}
    if name == "history":
        rows = canon.history(args["query"])
        return {
            "versions": [
                {
                    "fact": r["object"],
                    "status": r["status"],
                    "valid_at": str(r.get("valid_at", ""))[:19],
                    "invalid_at": (str(r["invalid_at"])[:19] if r.get("invalid_at") else None),
                }
                for r in rows
            ]
        }
    return {"error": f"unknown tool {name}"}


# --------------------------------------------------------------------------- #
# 3. A scripted four-turn trace (the store is real; the model is a script).   #
# --------------------------------------------------------------------------- #
def turn(canon: Canon, user: str, call: tuple[str, dict], answer: str) -> None:
    name, args = call
    print(f"\nuser> {user}")
    print(f"  assistant → tool_call {name}({json.dumps(args, ensure_ascii=False)})")
    result = execute_memory_tool(canon, name, args)
    print(f"  tool result ← {json.dumps(result, ensure_ascii=False)}")
    print(f"assistant> {answer}")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="feltstate_tools_"))
    canon = Canon(tmp / "canon.jsonl")

    print("=" * 72)
    print("memory as tools — remember / recall / correct / history, for real")
    print("=" * 72)

    turn(
        canon,
        "my sister's cat is called Miso - worth keeping.",
        ("remember", {"fact": "user's sister's cat is called Miso"}),
        "Kept. Miso it is.",
    )
    turn(
        canon,
        "wait - what was my sister's cat called again?",
        ("recall", {"query": "cat"}),
        "Miso - you told me earlier.",
    )
    turn(
        canon,
        "small correction: she renamed him Mochi last month.",
        ("correct", {"target": "cat", "corrected_fact": "user's sister's cat is called Mochi"}),
        "Updated - Mochi now. (I kept the old name on file, superseded.)",
    )
    turn(
        canon,
        "out of curiosity - what did you *used to* think his name was?",
        ("history", {"query": "cat"}),
        "Until today I believed Miso; since your correction it's Mochi. Both versions are on record with their validity windows.",
    )

    print("\n" + "=" * 72)
    print("Notes: recall bumped the fact's salience (used memory sticks);")
    print("correct() kept the old belief auditable instead of overwriting it -")
    print("that last 'history' answer is the part a context-stuffing memory")
    print("layer cannot give you.")
    print("=" * 72)


if __name__ == "__main__":
    main()
