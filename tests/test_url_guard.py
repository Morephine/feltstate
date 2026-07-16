"""URL scheme/host allow-listing for every outbound ``base_url``.

The affect source, the fact extractor and the reference reply backend all POST
to a caller-supplied endpoint over stdlib ``urllib`` — which would otherwise
open ``file://`` / ``ftp://`` / ``gopher://`` just as readily as HTTP. The guard
must reject anything that is not ``http``/``https`` with a host, *at construction*,
so a bad endpoint fails loudly before any request is made.
"""

import pytest

from feltstate._net import ALLOWED_URL_SCHEMES, require_http_url
from feltstate.companion.backends_ref import OpenAICompatBackend
from feltstate.memory.extract import LLMFactExtractor
from feltstate.sources.llm import LLMSource

# Schemes and shapes that must never reach urlopen.
_REJECTED = [
    "file:///etc/passwd",
    "file://localhost/etc/shadow",
    "ftp://example.com/x",
    "gopher://example.com:70/1",
    "data:text/plain,hello",
    "://no-scheme",
    "not a url",
    "",
    "   ",
]

# Well-formed endpoints that must be accepted unchanged.
_ACCEPTED = [
    "http://localhost:11434/v1",
    "https://api.example.com/v1",
    "https://example.com",
    "http://127.0.0.1:9/v1/",
]


def test_allowed_schemes_are_only_http_https():
    assert ALLOWED_URL_SCHEMES == ("http", "https")


@pytest.mark.parametrize("url", _ACCEPTED)
def test_require_http_url_accepts_http_and_https(url):
    assert require_http_url(url) == url


@pytest.mark.parametrize("url", _REJECTED)
def test_require_http_url_rejects_non_http(url):
    with pytest.raises(ValueError):
        require_http_url(url)


@pytest.mark.parametrize("url", _REJECTED)
def test_llm_source_rejects_bad_scheme_at_construction(url):
    with pytest.raises(ValueError):
        LLMSource(base_url=url, model="m")


@pytest.mark.parametrize("url", _REJECTED)
def test_fact_extractor_rejects_bad_scheme_at_construction(url):
    with pytest.raises(ValueError):
        LLMFactExtractor(base_url=url, model="m")


@pytest.mark.parametrize("url", _REJECTED)
def test_reply_backend_rejects_bad_scheme_at_construction(url):
    with pytest.raises(ValueError):
        OpenAICompatBackend(base_url=url, model="m")


def test_good_url_still_constructs_everything():
    # Sanity: the guard does not get in the way of a normal local endpoint.
    LLMSource(base_url="http://localhost:11434/v1", model="m")
    LLMFactExtractor(base_url="http://localhost:11434/v1", model="m")
    OpenAICompatBackend(base_url="http://localhost:11434/v1", model="m")
