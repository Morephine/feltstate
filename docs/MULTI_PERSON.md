# One soul, many people — relationship keying

The moment a second person can talk to the companion — a group chat, a
shared desktop, a friend borrowing the bridge — a design question appears:
does she become a different agent per person, or one person with different
relationships? This chapter is the pattern for the second answer, which is
the only one that stays coherent: **one memory, one feeling, many ledgers.**

---

## 1. The invariant: what is singular, what is plural

| | cardinality | why |
|---|---|---|
| mood, pressure, traits | **one** | she is one person; a rude stranger raises the same anger bar a rude friend would — feelings don't fork per audience |
| Canon (memory) | **one** | one archive of everything lived; entries record *who* (§4) |
| `Relationship` | **one per person** | closeness / trust / safety / tension / repair history are properties of a *pair*, not of her |

The library's `Relationship` (`feltstate/state.py`) carries exactly the five
axes a per-person ledger needs — `closeness`, `trust`, `safety`,
`unresolved_tension`, `repair_history` — and `repair_history`'s own docstring
shows why ledgers must not be shared: *"we have fought and come back before,
so a rough patch is survivable"* is true of one pair. Your history of repairs
is not transferable to someone she met yesterday.

## 2. The keying pattern

The engine holds one `relationship` slot; the app keys it:

```text
speaker id (platform user id, stable)         ← never display names
   │
   ├─ primary human  → the mainline key
   └─ anyone else    → a derived key ("discord:1234…")

per turn:
   1. resolve the speaker → key
   2. install that key's ledger into the engine   (Relationship.from_dict)
   3. run the turn — the felt block's relationship lines render THIS pair;
      appraisal's relationship effects land on THIS pair
   4. persist the ledger back under its key       (relationship.to_dict)
```

`Relationship.to_dict()` / `from_dict()` make the swap mechanical — a
ledger is a five-field dict in a keyed store. Key on platform ids, not
names: names collide, get edited, and are spoofable; ids are none of those.

## 3. The zero-pollution rule

**No speaker identity → no ledger.** Turns and fires that don't come from an
identified person — the frontend loop, introspection and dream fires, a game
shell feeding world state — install nothing and write nothing. An
introspection that accidentally ran on the mainline ledger would let her
private thinking *about* the relationship mutate the relationship's account
of itself; a group surface that defaulted unknown speakers onto the mainline
key would let strangers spend the primary pair's trust. Both are the same
bug: writes without an owner landing on someone's account.

Group chat is this rule run per message: every message resolves its own
speaker, every turn swaps in that speaker's ledger, and one person's context
never colours another's reply
([BRIDGE_ETIQUETTE.md](BRIDGE_ETIQUETTE.md) §5).

## 4. Memory stays whole

Canon does not fork per person — its 5W1H schema already records `who` on
every fact, so *"what do I know about this person"* is a *filter over one
store*, not a separate store. That is what lets her say "you two told me
opposite things about that" — a sentence impossible under per-person
archives.

One discretion rule rides on top: recall is whole, but *rendering* is
situated. What person B told her in private can inform how she acts; it
shouldn't be quoted back verbatim in person A's turn. That judgment lives in
the persona and the recall-rendering layer
([MEMORY_TOOLS.md](MEMORY_TOOLS.md)), not in the storage — she remembers
everything and repeats selectively, like anyone with two friends.

## 5. What the felt block shows each person

Only the relationship lines change per speaker — closeness phrasing, safety
phrasing, the tension line if that pair has one. Mood, pressure and time
render identically for everyone, because they *are* identical: if she's low
when a stranger says hi, the block says so, coloured by a thin ledger rather
than a warm one. One soul means the stranger meets the same person on a
different footing — not a fresh agent wearing the same face.

---

*See also:* [INTEGRATION.md](INTEGRATION.md) §8 — the surface layering this
keying lives inside · [BRIDGE_ETIQUETTE.md](BRIDGE_ETIQUETTE.md) §5 — the
bridge-side statement of the same isolation.
