"""File-change watching: snapshot + change detection."""
from __future__ import annotations

from stitchgraph.core.watch import changed, snapshot


def test_snapshot_and_change_detection(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.js").write_text("function f(){}\n")
    (tmp_path / "notes.txt").write_text("ignore me\n")
    s1 = snapshot(tmp_path)
    assert any(k.endswith("a.py") for k in s1)
    assert any(k.endswith("b.js") for k in s1)
    assert not any(k.endswith("notes.txt") for k in s1)  # non-source ignored

    assert not changed(s1, snapshot(tmp_path))            # nothing changed
    import os
    import time
    t = time.time() + 10
    os.utime(tmp_path / "a.py", (t, t))                    # touch -> modified
    assert changed(s1, snapshot(tmp_path))

    (tmp_path / "c.py").write_text("y = 2\n")              # added
    assert changed(s1, snapshot(tmp_path))
