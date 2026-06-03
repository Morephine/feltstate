"""feltstate.render.inject — feed the felt block back without busting the cache.

The whole point of feltstate is to hand the agent its own felt state each turn.
The naive way — splice it into the system prompt — quietly defeats prompt
caching and makes a persistent companion expensive. This module implements the
*cache-safe* alternative.

Why it matters
--------------
Providers cache the longest *unchanged prefix* of a request and bill cached
tokens at a fraction of the normal rate (and serve them faster). A persistent
companion sends a large, mostly static prefix every turn — persona, behavioural
rules, tools, long history. That prefix should be cached.

The felt block, by contrast, **changes every turn**. If it lived at the top
(in the system prompt), the cache prefix would end *before* it and everything
after would be re-billed at full price each turn. The mood line would silently
cost you the entire conversation.

The discipline
--------------
1. **Keep everything static at the top.** The system prompt — persona,
   behavioural rules, tools — must be byte-stable turn over turn so it is
   cached. Do **not** put the felt block there.
2. **Put the per-turn felt block at the *end*, in the latest user message.**
   It is the freshest, last-changing part of the request, after the cached
   prefix — so prepending it costs only the (small) uncached tail, not the
   whole context.
3. **Render in coarse bands** (see :mod:`feltstate.render.felt`) so that even
   the tail is usually byte-identical between adjacent ticks, extending cache
   reuse further.

Put together: a static, cached persona at the top; the agent's freshly felt
state riding in on the back of its newest user message. The agent reads the
block as its own feeling (identity-merge) and the cache stays warm.

This mirrors what the production system this was distilled from does: the felt
block is attached as a *dynamic prefix to the turn's instruction*, never folded
into the static system prompt that the cache pins.
"""

from __future__ import annotations


def build_injection(felt_block: str, user_message: str) -> str:
    """Attach a per-turn ``felt_block`` to the latest ``user_message``.

    The returned string is meant to be used as the **content of the current
    user turn** — i.e. it goes after the static, cached system/persona prefix,
    not inside it. The felt block is placed first (as a dynamic prefix to the
    turn) followed by the user's actual words, so the agent reads its own felt
    state immediately before responding to what was said.

    Parameters
    ----------
    felt_block
        A first-person felt block, e.g. from
        :func:`feltstate.render.felt.render_felt_block`. If empty/whitespace,
        the user message is returned unchanged (nothing to inject).
    user_message
        The user's actual message for this turn.

    Returns
    -------
    str
        ``felt_block`` + a blank-line separator + ``user_message``. When
        ``felt_block`` is empty, just ``user_message``.

    Notes
    -----
    **Do not** instead splice ``felt_block`` into the system prompt: that block
    changes every turn and would invalidate the cached prefix, re-billing the
    whole context each turn. Keeping it in the user message — the last,
    already-uncached part of the request — is what keeps a persistent companion
    cheap. See the module docstring.
    """
    block = (felt_block or "").strip()
    if not block:
        return user_message
    return f"{block}\n\n{user_message}"
