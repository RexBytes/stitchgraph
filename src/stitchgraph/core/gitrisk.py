"""Git-history fusion (design §6.H). Churn × centrality, and hidden coupling.

Two signals the static graph can't see on its own:

- **Risk hotspots** — files that change often (churn) *and* are depended on a lot
  (centrality). High × high is where bugs are most expensive.
- **Hidden coupling** — files that change *together* in git but have **no**
  structural edge between them. That's an implicit dependency the call/import
  graph misses entirely — the most valuable thing git history adds.

Uses `git log` via subprocess; if the path isn't a git repo, callers refuse
cleanly. File paths from git are matched to node ids (`<file>::...`) by the
file prefix, so this assumes the indexed root is the repo root.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from itertools import combinations
from pathlib import Path


def toplevel(path: str | Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def is_git_repo(path: str | Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _commits(path: str | Path, max_commits: int = 2000) -> list[list[str]]:
    """Return, per commit, the list of files it touched (most recent first)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "log", f"-{max_commits}", "--no-merges",
             "--format=%x00", "--name-only"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    commits: list[list[str]] = []
    for block in r.stdout.split("\x00"):
        files = [ln.strip() for ln in block.splitlines() if ln.strip()
                 and ln.strip().endswith(".py")]
        if files:
            commits.append(files)
    return commits


def churn(path: str | Path) -> dict[str, int]:
    """Per-file commit count (how often each file changes)."""
    counts: Counter[str] = Counter()
    for files in _commits(path):
        counts.update(set(files))
    return dict(counts)


def cochange(path: str | Path, min_shared: int = 2) -> dict[frozenset[str], int]:
    """Per file-pair count of commits that touched both."""
    pairs: Counter[frozenset[str]] = Counter()
    for files in _commits(path):
        uniq = sorted(set(files))
        if 2 <= len(uniq) <= 30:  # skip giant sweeping commits (noise)
            for a, b in combinations(uniq, 2):
                pairs[frozenset((a, b))] += 1
    return {k: v for k, v in pairs.items() if v >= min_shared}
