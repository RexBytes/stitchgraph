"""Report adapter (design §8): point at an index, get a Markdown report.

The report is just a fourth renderer over the same operations, organised into the
urgency tiers (Fix now / Look closer / Cleanup). Stdlib-only.
"""

from __future__ import annotations

from ..core import operations as ops
from ..core.store import Store


def build_report(db: str = "stitchgraph.db") -> str:
    with Store(db) as store:
        orient = ops.orient(store)
        holes = ops.find_holes(store)
        stale = ops.find_stale(store)

    out: list[str] = ["# stitchgraph report", ""]
    out += ["## Orientation", ""]
    counts = (orient.result or {}).get("node_counts", {})
    out.append(f"- Nodes indexed: {orient.meta.get('total_nodes', 0)}")
    out.append(f"- By kind: {counts or '(none)'}")
    hubs = (orient.result or {}).get("top_hubs", [])
    out.append(f"- Read these first (hubs): {[h['id'] for h in hubs] or '(none)'}")
    out.append("")

    out += ["## \U0001f7e0 Look closer", ""]
    hole_list = holes.result or []
    out.append(f"- Implementation holes (dangling references): {len(hole_list)}")
    for h in hole_list[:20]:
        out.append(f"  - {h['src']} -> missing `{h['missing']}` ({h['relation']})")
    out.append("")

    out += ["## \U0001f7e2 Cleanup (UNVERIFIED until entry-point detection lands)", ""]
    stale_list = stale.result or []
    out.append(f"- Stale candidates: {len(stale_list)}")
    if stale.needs_review:
        for r in stale.review_reasons:
            out.append(f"  - note: {r}")
    out.append("")
    return "\n".join(out)


def main() -> None:
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "stitchgraph.db"
    print(build_report(db))


if __name__ == "__main__":
    main()
