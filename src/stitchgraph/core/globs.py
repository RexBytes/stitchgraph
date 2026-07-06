"""Root-anchored, gitignore-style matching for `[index] ignore` globs.

Until v3.37.0 both extractors matched ignore patterns with `PurePath.match`,
whose semantics are the opposite of what every `ignore = [...]` author expects:
it anchors at the RIGHT (so `script/**` swallowed `homeassistant/components/
script/*` — any path whose trailing segments fit), and before Python 3.13 `**`
is just `*` (so `tests/components/**` failed to ignore anything nested more
than one level deep). The 2026-07-05 Home Assistant field run (research/18)
hit both directions at once: 6,627 files indexed against the config's intent
AND 6 files wrongly dropped.

The contract here is the familiar gitignore one, applied to the root-relative
POSIX path of each candidate file:

- Patterns containing a `/` are **anchored at the indexed root**:
  `script/**` means the top-level `script/` tree and nothing else.
- `**` matches any number of whole path segments; `*` and `?` never cross a
  `/`; `[...]` character classes work within a segment.
- A pattern with no `/` matches a **basename or directory name anywhere**
  (`*.min.js`, `__snapshots__`) — the one convenience the right-anchored era
  actually delivered, kept on purpose.
- A pattern that matches a directory ignores everything beneath it
  (`vendor/fixtures` needs no trailing `/**`).
- Empty patterns are skipped (a hand-edited `ignore = [""]` must not crash —
  panel R33B).

Matching anywhere-in-the-tree is a leading `**/` away (`**/vendor/*`), so no
expressiveness is lost by anchoring — only surprises.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import PurePath

__all__ = ["ignored"]


def _segment_rx(seg: str) -> str:
    """One path segment of a glob → regex that cannot cross a '/'."""
    out: list[str] = []
    i = 0
    while i < len(seg):
        ch = seg[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            # find the closing bracket, honouring a leading negation and a
            # literal ']' right after it (fnmatch's rules)
            j = i + 1
            if j < len(seg) and seg[j] in ("!", "^"):
                j += 1
            if j < len(seg) and seg[j] == "]":
                j += 1
            while j < len(seg) and seg[j] != "]":
                j += 1
            if j >= len(seg):  # unbalanced '[' — treat as literal
                out.append(re.escape(ch))
            else:
                inner = seg[i + 1:j]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                out.append(f"[{inner}]")
                i = j
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


@lru_cache(maxsize=1024)
def _compile(pattern: str) -> re.Pattern[str] | None:
    pat = pattern.strip("/")
    if not pat:
        return None
    segs = pat.split("/")
    parts: list[str] = []
    for k, seg in enumerate(segs):
        last = k == len(segs) - 1
        if seg == "**":
            # zero or more whole segments; as the tail it means "anything below"
            parts.append(".*" if last else "(?:[^/]+/)*")
        else:
            parts.append(_segment_rx(seg) + ("" if last else "/"))
    body = "".join(parts)
    if "/" in pat:
        # anchored at the indexed root; a directory match ignores its subtree
        return re.compile(f"^{body}(?:/.*)?$")
    # no '/': a basename / directory-name match anywhere in the tree
    return re.compile(f"^(?:.*/)?{body}(?:/.*)?$")


def ignored(rel: str | PurePath, patterns: list[str] | None) -> bool:
    """True if the root-relative path `rel` matches any ignore pattern."""
    if not patterns:
        return False
    posix = rel.as_posix() if isinstance(rel, PurePath) else PurePath(rel).as_posix()
    for pattern in patterns:
        rx = _compile(pattern)
        if rx is not None and rx.match(posix):
            return True
    return False
