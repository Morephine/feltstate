"""A redirect must not carry the API key to another origin.

``urllib`` follows 301/302/303 and keeps ``Authorization`` when it does, so a
misconfigured or hijacked endpoint could hand the key to whatever host the
redirect names. ``feltstate._net.open_url`` drops credential headers on a
cross-origin hop and keeps them on a same-origin one.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from feltstate._net import open_url

SECRET = "Bearer sk-test-secret"


class _Target(BaseHTTPRequestHandler):
    seen: dict = {}

    def do_GET(self):  # noqa: N802 - stdlib naming
        type(self).seen["auth"] = self.headers.get("Authorization")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence the test run
        pass


def _serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _redirector(location):
    class _R(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, *args):
            pass

    return _R


@pytest.fixture
def target():
    _Target.seen = {}
    srv = _serve(_Target)
    yield srv
    srv.shutdown()


def _get(url):
    req = urllib.request.Request(url, headers={"Authorization": SECRET})
    with open_url(req, timeout=5) as resp:
        resp.read()


def test_cross_origin_redirect_drops_authorization(target):
    port = target.server_port
    # 'localhost' vs '127.0.0.1' is a different host: a different origin.
    hop = _serve(_redirector(f"http://localhost:{port}/x"))
    try:
        _get(f"http://127.0.0.1:{hop.server_port}/v1/chat/completions")
    finally:
        hop.shutdown()
    assert _Target.seen["auth"] is None


def test_same_origin_redirect_keeps_authorization():
    """An ordinary path rewrite on your own endpoint must still authenticate."""

    class _SelfRedirect(_Target):
        def do_GET(self):  # noqa: N802
            if self.path == "/start":
                host = self.headers.get("Host")
                self.send_response(302)
                self.send_header("Location", f"http://{host}/moved")
                self.end_headers()
                return
            super().do_GET()

    _Target.seen = {}
    srv = _serve(_SelfRedirect)
    try:
        _get(f"http://127.0.0.1:{srv.server_port}/start")
    finally:
        srv.shutdown()
    assert _Target.seen["auth"] == SECRET


def test_plain_urlopen_would_have_leaked(target):
    """Guard the guard: stdlib really does leak, so the test above means something."""
    port = target.server_port
    hop = _serve(_redirector(f"http://localhost:{port}/x"))
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{hop.server_port}/v1", headers={"Authorization": SECRET}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.URLError:  # pragma: no cover - network hiccup
        pytest.skip("loopback redirect unavailable")
    finally:
        hop.shutdown()
    assert _Target.seen["auth"] == SECRET
