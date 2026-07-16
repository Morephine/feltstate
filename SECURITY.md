# Security Policy

## Reporting a vulnerability

Please report security issues **privately** to the maintainer (e.g. via a GitHub
security advisory on this repository) rather than opening a public issue. You can
expect an acknowledgement and a discussion of next steps.

## Threat model

`feltstate` is a local library: no server, no daemon it starts on its own, and no
bundled secrets. It runs inside *your* application and inherits that process's
trust boundary. The notes below are meant to be honest about what it does and
does **not** protect against, so you can place it correctly in a larger system.

### Local state is unencrypted at rest

`AffectState`, `Canon` stores, memory-lifecycle ledgers, dreams, and diaries are
written as **plain JSON / JSONL on the local filesystem**. There is no
encryption, no access control, and no obfuscation. These files accumulate an
agent's feelings and remembered facts about a specific person and should be
treated as sensitive user data:

- Restrict them with filesystem permissions; encrypt the volume if the threat
  model needs at-rest protection.
- The bundled `.gitignore` keeps the default state/history paths out of version
  control — do not override that and commit a live store.
- Backups and snapshots inherit the same sensitivity (see *Deletion*, below).
- Corrupt-state quarantine files (`*.corrupt`, `*.corrupt-*`) may contain exact
  copies of rejected memory/state rows for diagnosis. Protect and retire them
  with the same policy as the live store; quarantine is evidence preservation,
  not redaction.

### Data sent to a hosted LLM endpoint

The optional network components — `LLMSource` (affect estimation),
`LLMFactExtractor` (fact proposal), and the reference `OpenAICompatBackend`
(reply generation) — POST to whatever `base_url` you configure. What leaves the
machine:

- **A slice of the conversation transcript** (recent turns, trimmed) plus the
  latest user message, and any **persona** text you supply. The reply backend in
  particular is handed the message list your application gives it, which can be
  the **full conversation history**.
- A short numeric summary of the agent's affect baseline.

If you point these at a hosted API, that provider receives the above. **Point
them at a local endpoint if the data must not leave the machine.** As a guard
against gross misconfiguration, `base_url` must be an `http`/`https` URL with a
host; other schemes (`file://`, `ftp://`, `gopher://`, ...) are rejected at
construction. This is a scheme/host allow-list only — it does **not** defend
against SSRF to internal hosts, DNS rebinding, or malicious redirects. If the
endpoint itself is untrusted, that is your threat to manage.

### Prompt injection and model-output-to-prompt

feltstate renders a first-person "felt block" that you prepend to the reply
model's context. Two directions of risk:

- **User → prompt.** User messages are untrusted input. feltstate does not, and
  cannot, sanitise them into safety; standard prompt-injection defences on the
  reply model remain your responsibility.
- **Model output → prompt.** Affect is *estimated* by a model whose output flows
  back into the felt block, so that output is itself an injection surface.
  `LLMSource` labels are restricted to a fixed known vocabulary. `VheartSource`
  uses an intentionally open label set, so its labels and `mixed_blend` names are
  instead bounded to short, single-line, alphanumeric-ish tokens — anything with
  newlines, control characters, or markup-breaking punctuation is dropped. The
  free-text **monologue** field is *not* charset-restricted (it is meant to be a
  sentence); treat it as untrusted model text and quote/escape it when you place
  it, exactly as you would any generated string.

### Concurrent writes and crash recovery

- **Concurrency.** A foreground turn and a background heartbeat can write the
  same store at once. `Canon` serialises writes per path with a two-layer lock:
  an in-process threading lock (the common case) plus a best-effort advisory
  file lock underneath (covers a second *process*, where the OS supports it).
  The advisory lock is best-effort — on platforms/filesystems without it, cross-
  process safety degrades, so avoid pointing two separate processes at one store.
- **Crash recovery.** The lifecycle reaper writes an fsynced pending ledger
  before it moves any row and clears it only after the cascade completes; call
  `replay_if_pending` on boot to finish an interrupted deletion idempotently
  (keyed by a transaction id). A malformed pending record fails closed — it is
  left in place and raised, never silently discarded. This targets power-loss
  mid-deletion, not concurrent writers racing the reaper.

### Data deletion and archive guarantees

- **What is honoured.** A memory judged dead is physically removed from the live
  store **and from every snapshot you explicitly hand the reaper** — forgetting
  covers the snapshots passed to that call, on purpose. The tamper-evident
  `chain` distinguishes a lawful (tombstoned) death from a silent evaporation.
- **Honest limits.** Several important constraints apply:
  - Source-material / raw-archive rows are *marked* eligible for deletion but
    **not yet physically purged** — that adapter is future work, so raw source
    text a fingerprint points at can outlive the distilled memory until you purge
    it yourself.
  - Snapshots feltstate is never told about cannot be purged; only the snapshots
    you explicitly pass to the reaper are touched.
  - Deletion is **not** a cryptographic erase: assume forensic recovery of freed
    disk blocks is possible unless the underlying storage prevents it.
  - Backups managed outside this library (e.g. cloud backup, filesystem
    snapshots) are not reachable by feltstate and must be managed separately.

### Model revision and third-party weight supply chain

`VheartSource` downloads a fine-tuned adapter and its base model from the
Hugging Face Hub at runtime; **it executes third-party model code/weights inside
your process** via `transformers`/`peft`. Loading an untrusted repo is a code-
execution risk. Pin an immutable commit with `revision=` (adapter) and
`base_revision=` (base) rather than tracking a mutable branch like `"main"`,
which follows whatever the repo owner pushes next. `THIRD_PARTY_NOTICES.md`
records the referenced model, its base, and their licences. The core library and
its other reference sources pull **no** third-party dependencies.

### Presence-probe fail-open

The proactive `CompanionScheduler` gates on a `UserPresenceAdapter`. Through the
gate helpers these probes **fail open**: a broken or throwing probe is treated as
"user not busy" / "idle for a long time" so the companion keeps living rather
than freezing on a down dependency. The consequence to know: if your presence
probe breaks, the scheduler may initiate proactively at a time it would normally
stay quiet. If silence-on-failure matters more than liveness for your
deployment, wrap or replace the adapter so it fails closed.
