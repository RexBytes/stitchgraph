"""Resolution seams: context managers and property reads keep code live."""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg


def _build(root: Path, body: str) -> sg.Store:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('from .m import entry\n__all__ = ["entry"]\n')
    (pkg / "m.py").write_text(body)
    store = sg.Store(":memory:")
    sg.reindex(store, str(root))
    return store


def test_context_manager_cleanup_stays_live(tmp_path):
    body = (
        "class Res:\n"
        "    def __enter__(self):\n        return self\n"
        "    def __exit__(self, *a):\n        self.cleanup()\n"
        "    def cleanup(self):\n        return 1\n"
        "    def use(self):\n        return 2\n\n"
        "def entry():\n"
        "    with Res() as r:\n"
        "        return r.use()\n"
    )
    with _build(tmp_path, body) as store:
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        # __exit__ is referenced by `with`; it calls cleanup -> cleanup is live.
        assert "cleanup" not in stale
        assert "use" not in stale


def test_property_read_stays_live(tmp_path):
    body = (
        "class Box:\n"
        "    @property\n"
        "    def ready(self):\n        return True\n"
        "    def check(self):\n        return self.ready\n\n"  # attr read, not call
        "def entry():\n"
        "    return Box().check()\n"
    )
    with _build(tmp_path, body) as store:
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "ready" not in stale  # property read via self.ready -> live
