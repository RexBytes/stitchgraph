"""v3.40.0: the parallel-extraction differential oracle. The fork-pool path must
be BYTE-IDENTICAL to the serial reference — same nodes, same edges, same ORDER —
including the awkward inhabitants: a syntax-failed file (skip parity), a PEP 695
file (fallback interplay), mutable globals (pass-2 VARIABLE nodes), framework
bases (pass-2 external_base_classes), fixtures, and protocol dunders."""

from __future__ import annotations

import sys

import pytest

from stitchgraph.core.extract import extract_project

pytestmark = pytest.mark.skipif(sys.platform != "linux",
                                reason="parallel extraction is fork/Linux-only")


def _tree(tmp_path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .svc import Service\n__all__ = ['Service']\n")
    (pkg / "svc.py").write_text(
        "import threading\n"
        "COUNTER = {}\n"                                    # mutable global -> VARIABLE node
        "class Service(threading.Thread):\n"                # external base -> framework
        "    def run(self):\n"
        "        COUNTER['x'] = 1\n"
        "        with self.lock:\n"
        "            return helper()\n\n"
        "def helper():\n    return 1\n")
    (pkg / "modern.py").write_text("type Alias = int\n\ndef modern_fn():\n    return 1\n")
    (pkg / "broken.py").write_text("def broken(:\n")        # genuine syntax error, all versions
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef env():\n    return {}\n")
    (tmp_path / "test_app.py").write_text(
        "def test_run(env):\n    assert env == {}\n")
    for i in range(8):                                       # a little width for the pool
        (tmp_path / f"m{i}.py").write_text(
            f"def f{i}(x):\n    return x.process()\n\ndef g{i}():\n    return f{i}(1)\n")
    return tmp_path


def _snapshot(root, parallel):
    report: dict = {}
    nodes, edges = extract_project(str(root), report=report, parallel=parallel)
    n = [(x.id, x.kind, x.name, tuple(sorted(x.roles)), x.location) for x in nodes]
    e = [(x.src, x.relation, x.dst_symbol, x.dst_id, x.weight, x.provenance,
          x.name_based) for x in edges]
    return n, e, (report.get("skipped"), report.get("fallback"))


def test_parallel_is_byte_identical_to_serial(tmp_path):
    root = _tree(tmp_path)
    sn, se, ss = _snapshot(root, parallel=False)
    pn, pe, ps = _snapshot(root, parallel=True)
    assert ps == ss          # skip reporting, same order
    assert pn == sn          # nodes, same order (incl. pass-2 VARIABLE nodes)
    assert pe == se          # edges, same order


def test_parallel_with_edge_sink_matches(tmp_path):
    root = _tree(tmp_path)

    s_sink: list = []
    _, _ = extract_project(str(root), edge_sink=s_sink, parallel=False)
    p_sink: list = []
    _, _ = extract_project(str(root), edge_sink=p_sink, parallel=True)
    key = lambda x: (x.src, x.relation, x.dst_symbol, x.dst_id or "", x.weight)  # noqa: E731
    assert [key(x) for x in p_sink] == [key(x) for x in s_sink]
