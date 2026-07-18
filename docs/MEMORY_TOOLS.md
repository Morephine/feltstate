# Memory as tools — let the model dig for itself

Context-stuffing pushes every memory into every prompt and hopes relevance
sorts itself out. The tool-surface pattern inverts it: the reply model gets
**five function-calling tools** and decides, mid-conversation, when to store,
search, fix, or audit its own memory. The store's lifecycle properties —
salience, decay, supersession, the audit trail — do the rest.

Everything below is real: the schemas, the dispatcher, and the quoted trace are
the verbatim plumbing and output of
[`examples/memory_tools.py`](../examples/memory_tools.py) (zero network — only
the "model" side of the trace is scripted; wire the same schemas into any
function-calling LLM and delete the script).

---

## The five tools

| tool | store call | what makes it different |
|---|---|---|
| `remember(fact, why?)` | `canon.add()` | repeat-adds *reinforce* instead of duplicating — a fact told twice decays slower |
| `recall(query)` | `canon.search()` | a hit **bumps salience**: used memory sticks, unused memory fades toward the archive |
| `correct(target, corrected_fact)` | `canon.correct()` | supersedes, never overwrites — the old belief stays on file with its validity window |
| `retract(target)` | `canon.retract()` | un-says a fact without deleting the record that it was held |
| `history(query)` | `canon.history()` | the bi-temporal audit trail: every version, `active / superseded / retracted`, with `valid_at` / `invalid_at` |

The JSON schemas (OpenAI tool format, copy as-is) live at the top of the
example — `MEMORY_TOOLS`. The dispatcher an app writes is ~30 lines
(`execute_memory_tool`): map name → store call → JSON-safe result.

## The trace — store, recall, correct, and "what did I used to think?"

Verbatim output:

```text
user> my sister's cat is called Miso - worth keeping.
  assistant → tool_call remember({"fact": "user's sister's cat is called Miso"})
  tool result ← {"kept": "user's sister's cat is called Miso", "id": "6d6ae691db5613ff010c003322f3f9d7"}
assistant> Kept. Miso it is.

user> wait - what was my sister's cat called again?
  assistant → tool_call recall({"query": "cat"})
  tool result ← {"hits": [{"fact": "user's sister's cat is called Miso", "salience": 0.52, "id": "6d6ae691db5613ff010c003322f3f9d7"}]}
assistant> Miso - you told me earlier.

user> small correction: she renamed him Mochi last month.
  assistant → tool_call correct({"target": "cat", "corrected_fact": "user's sister's cat is called Mochi"})
  tool result ← {"corrected_to": "user's sister's cat is called Mochi", "old_version_kept": true}
assistant> Updated - Mochi now. (I kept the old name on file, superseded.)

user> out of curiosity - what did you *used to* think his name was?
  assistant → tool_call history({"query": "cat"})
  tool result ← {"versions": [
    {"fact": "user's sister's cat is called Miso",  "status": "superseded", "valid_at": "2026-07-18T12:27:22", "invalid_at": "2026-07-18T12:27:22"},
    {"fact": "user's sister's cat is called Mochi", "status": "active",     "valid_at": "2026-07-18T12:27:22", "invalid_at": null}]}
assistant> Until today I believed Miso; since your correction it's Mochi. Both versions are on record with their validity windows.
```

That last turn is the point. A retrieval index can answer "what do you know
about the cat?" — only a store with supersession and validity windows can
answer **"what did you *used to* believe, and when did that stop being
true?"** For a companion, that is the difference between a search box and a
memory.

## Wiring notes

* **Where it sits in the loop:** the tools ride the same reply-model call as
  everything else (see the wiring diagram in
  [INTEGRATION.md](INTEGRATION.md#1-the-wiring-diagram)). The felt block is
  *pushed* context; memory is *pulled* on demand. Both can coexist with a
  small always-on digest (e.g. 2–3 highest-salience facts) if you want warm
  recall without a tool round-trip.
* **System-prompt line to pair with the schemas:** tell the model what the
  store is for — "you have durable memory tools; store sparingly (one plain
  sentence per fact), recall before claiming you remember, correct instead of
  contradicting yourself."
* **Trust boundary:** the tools mutate a real store. If several people can
  talk to the companion, decide *whose* facts land where before exposing
  `remember` — and never let one speaker's `recall` surface another's private
  facts (see INTEGRATION.md §7).
* **Sparse is healthy:** salience decay is the garbage collector. Facts nobody
  recalls drift to the archive; `compact()` (run it periodically) moves the
  dim ones out of the hot store with a crash-safe, archive-first order.
