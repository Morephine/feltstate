"""feltstate._net — a small internal guard for outbound HTTP endpoints.

Several optional components take a caller-supplied ``base_url`` and POST to it
over stdlib :mod:`urllib` (the affect :class:`~feltstate.sources.llm.LLMSource`,
the :class:`~feltstate.memory.extract.LLMFactExtractor`, and the reference
:class:`~feltstate.companion.backends_ref.OpenAICompatBackend`). ``urllib`` will
happily open ``file://``, ``ftp://``, ``gopher://`` and similar schemes, so a
misconfigured or attacker-influenced URL could turn a "call my LLM" into a local
file read or an SSRF-style probe.

This module centralises one rule: **only ``http`` and ``https`` are allowed**,
and the URL must name a host. It is a configuration guard, not a network policy —
it does not resolve DNS, block private IP ranges, or inspect redirects. Pin the
endpoint you trust; this just rejects the obviously-wrong scheme early, with a
clear error, instead of letting ``urllib`` act on it.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# The only schemes an OpenAI-compatible endpoint should ever use.
ALLOWED_URL_SCHEMES = ("http", "https")


def require_http_url(url: str) -> str:
    """Return ``url`` unchanged if it is a well-formed ``http``/``https`` URL.

    Raises :class:`ValueError` otherwise — a non-string, an empty string, a
    scheme outside :data:`ALLOWED_URL_SCHEMES` (``file://``, ``ftp://``,
    ``gopher://``, ...), or a URL with no host. Validate the *base* URL you were
    handed at construction time so a bad endpoint fails loudly and early rather
    than being passed to :func:`urllib.request.urlopen`.

    This is a scheme/host allow-list only. It deliberately does not defend
    against SSRF to internal hosts, DNS rebinding, or malicious redirects; if
    the endpoint is untrusted, that is the caller's threat to manage.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("endpoint URL must be a non-empty string")
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"endpoint URL scheme must be one of {ALLOWED_URL_SCHEMES}, "
            f"got {parts.scheme!r} in {url!r} (file://, ftp://, gopher:// and the "
            "like are refused)"
        )
    if not parts.hostname:
        raise ValueError(f"endpoint URL must include a host, got {url!r}")
    return url
