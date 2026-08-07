"""feltstate.render.channels — machine-readable channel tags for surfaced memories.

When a memory surfaces — an anchor note the companion always carries, a random
emergence, an affect-spike flashback, a resonance with what is being said — the
line usually crosses a pipe boundary on its way to the reply model: a compose
step prints it, an inject step filters it, a renderer prettifies it.

The failure this module prevents is quiet and expensive: **a pipeline that
filters on pretty formatting dies the day the formatting gets prettier.** If
the inject step recognises memory lines by their opening quote mark, then the
day the renderer switches quote style, every downstream mouth goes silent —
no error, no log line, cooldowns still burning — and nobody notices until
someone asks why the companion stopped remembering things out loud.

So: **producers speak a machine protocol; pipes filter on the protocol;
humans get pretty text at the last hop only.** A surfaced line is tagged with
a short channel prefix::

    EMG:she mentioned the maze game again
    SPK:the night the save file corrupted
    RSN:this is like the rent dispute last spring
    RES:always carries: the promise about backups

``tag`` and ``parse`` are the whole protocol. Renderers may do anything they
like to the payload *after* ``parse`` — the prefix is for pipes, not for
people, and it never reaches the reply model's context.

Channels
--------
* ``RESIDENT`` (``RES``) — carried notes: always-on anchors, standing context.
* ``EMERGENT`` (``EMG``) — unprompted surfacing: the dice landed on a memory.
* ``FLASHBACK`` (``SPK``) — affect-spike recall: feeling first, memory after.
* ``RESONANT`` (``RSN``) — triggered by the current topic: "this is like...".

The set is deliberately small and closed: a channel is a *routing decision*
(what may interrupt, what must wait, what renders where), and routing decisions
multiply badly. Applications wanting sub-flavours should put them in the
payload, not the prefix.
"""

from __future__ import annotations

__all__ = [
    "CHANNELS",
    "EMERGENT",
    "FLASHBACK",
    "RESIDENT",
    "RESONANT",
    "parse",
    "tag",
]

RESIDENT = "RES"
EMERGENT = "EMG"
FLASHBACK = "SPK"
RESONANT = "RSN"

#: The closed set, prefix -> human name (for logs and docs, not for prompts).
CHANNELS: dict[str, str] = {
    RESIDENT: "resident",
    EMERGENT: "emergent",
    FLASHBACK: "flashback",
    RESONANT: "resonant",
}

_SEP = ":"


def tag(channel: str, payload: str) -> str:
    """One protocol line: ``"EMG:the payload"``.

    ``channel`` must be one of the closed set; anything else raises — an
    unknown channel is a routing decision nobody made, and inventing one
    silently is how protocols rot. The payload is kept verbatim except that
    newlines become spaces (the protocol is line-oriented; a payload that
    spans lines would smuggle untagged lines into the pipe).
    """
    ch = str(channel).strip().upper()
    if ch not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; expected one of {sorted(CHANNELS)}")
    flat = " ".join(str(payload).splitlines()).strip()
    return f"{ch}{_SEP}{flat}"


def parse(line: str) -> tuple[str, str] | None:
    """The pipe's side of the protocol: ``(channel, payload)`` or ``None``.

    ``None`` means "not a protocol line" — narration, blank lines, pretty
    text — and the correct pipe behaviour is to pass or drop it *by policy*,
    never to guess at its meaning. Matching is strict: a known prefix,
    immediately followed by the separator, at position zero. ``"EMG :"``,
    ``"emg:"`` and ``"xEMG:"`` are not protocol lines; leniency here is how
    filters drift back into format-sniffing.
    """
    if not isinstance(line, str):
        return None
    head, sep, tail = line.partition(_SEP)
    if not sep or head not in CHANNELS:
        return None
    return head, tail.strip()
