"""Report adapter (design §8): point at an index, get a Markdown report.

The report is a fourth renderer over the same operations, organised into the
urgency tiers (Fix now / Look closer / Cleanup). Stdlib-only.
"""

from __future__ import annotations

from ..core import operations as ops
from ..core.store import Store


def build_report(db: str = "stitchgraph.db", repo: str | None = None) -> str:
    with Store(db) as store:
        orient = ops.orient(store)
        scan = ops.scan(store)
        stale = ops.find_stale(store)
        # repo=None lets risk() default to the indexed root recorded in the DB, so
        # `report --db <db>` includes the risk section from any cwd (issue #18).
        risk = ops.risk(store, repo)

    issues = scan.result or []
    by_urgency = {u: [i for i in issues if i["urgency"] == u]
                  for u in ("red", "orange", "green")}

    out: list[str] = ["# stitchgraph report", ""]

    out += ["## Orientation", ""]
    counts = (orient.result or {}).get("node_counts", {})
    out.append(f"- Nodes indexed: {orient.meta.get('total_nodes', 0)}")
    out.append(f"- By kind: {counts or '(none)'}")
    hubs = (orient.result or {}).get("top_hubs", [])
    out.append("- Read these first (hubs): "
               f"{[h['id'].split('::')[-1] for h in hubs[:8]] or '(none)'}")
    out.append("")

    out += ["## \U0001f534 Fix now", ""]
    _emit_issues(out, by_urgency["red"])

    out += ["", "## \U0001f7e0 Look closer", ""]
    _emit_issues(out, by_urgency["orange"])

    out += ["", "## \U0001f7e2 Cleanup", ""]
    stale_list = stale.result or []
    out.append(f"- Stale code candidates: {len(stale_list)} "
               f"(confidence {stale.confidence:.2f}, verify before removing)")
    for c in stale_list[:20]:
        out.append(f"  - {c['id']}")
    _emit_issues(out, by_urgency["green"])

    out += ["", "## Risk (git × structure)", ""]
    if risk.ok:
        for h in (risk.result or {}).get("hotspots", [])[:5]:
            out.append(f"- {h['urgency']} {h['file']} "
                       f"(churn {h['churn']}, risk {h['risk']})")
        hidden = (risk.result or {}).get("hidden_coupling", [])
        if hidden:
            out.append(f"- Hidden coupling pairs: {len(hidden)} "
                       "(co-change with no structural edge)")
    else:
        out.append(f"- (skipped: {risk.review_reasons[0] if risk.review_reasons else 'no git'})")

    return "\n".join(out)


def _emit_issues(out: list[str], issues: list[dict]) -> None:
    if not issues:
        out.append("- (none)")
        return
    for i in issues[:25]:
        node = i.get("node", "").split("::")[-1]
        out.append(f"- **{i['kind']}** `{node}` — {i.get('reason', '')}")


def main() -> None:
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "stitchgraph.db"
    repo = sys.argv[2] if len(sys.argv) > 2 else None
    print(build_report(db, repo))


if __name__ == "__main__":
    main()
