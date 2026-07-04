"""find_component — the purpose-aware component locator (IDEAS §3, productised).

Pins the two ingredients the research ablation proved out (research/05-archetype-
purpose): test code never surfaces (by role AND by test-file path), and exported
public API outranks a same-vocabulary internal helper via the score boost."""
from __future__ import annotations

import textwrap

import stitchgraph as sg


def _index(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "client.py").write_text(textwrap.dedent('''
        class Session:
            def request(self, method, url):
                """Send an HTTP request and return the response."""
                return _prepare_request(method, url)

        def _prepare_request(method, url):
            """Build the raw http request payload."""
            return (method, url)
    '''))
    (root / "__init__.py").write_text("from .client import Session\n")
    (root / "test_client.py").write_text(textwrap.dedent('''
        def test_send_http_request_returns_response():
            """Sends an http request and checks the response."""
            assert True
    '''))
    store = sg.Store(str(tmp_path / "idx.db"))
    assert sg.reindex(store, str(root)).ok
    return store


def test_public_component_wins_and_tests_are_excluded(tmp_path):
    store = _index(tmp_path)
    r = sg.find_component(store, "send an http request and return the response")
    assert r.ok
    ids = [item["id"] for item in r.result]
    assert not any("test_client" in i for i in ids), "test code must never surface"
    # the exported public method must outrank the internal helper that shares
    # its vocabulary — that ordering IS the feature (research: 59% -> 76% P@1)
    request_rank = next(i for i, item in enumerate(r.result)
                        if item["name"] == "request")
    helper_ranks = [i for i, item in enumerate(r.result)
                    if item["name"] == "_prepare_request"]
    assert all(request_rank < h for h in helper_ranks)
    assert r.result[request_rank]["exported"] is True


def test_boost_is_what_wins_it(tmp_path):
    """Falsification arm: with the boost off, ranking follows raw similarity only —
    the public symbol has no edge. The boost must be doing the work."""
    store = _index(tmp_path)
    boosted = sg.find_component(store, "send an http request and return the response")
    flat = sg.find_component(store, "send an http request and return the response",
                             public_boost=0.0)
    b = {i["id"]: i["score"] for i in boosted.result}
    f = {i["id"]: i["score"] for i in flat.result}
    exported = [i["id"] for i in boosted.result if i["exported"]]
    assert exported and all(b[e] > f[e] for e in exported if e in f)


def test_refusals(tmp_path):
    store = _index(tmp_path)
    assert not sg.find_component(store, "").ok
    assert not sg.find_component(store, "send a request", limit=0).ok
    assert not sg.find_component(sg.Store(":memory:"), "anything").ok
