"""Single-file extraction against the persisted symbol table (research/21).

`extract_single_file(store, root, rel)` runs the Python extractor's two passes
over ONE file, resolving references against store-backed views of the
whole-project symbol tables — `nodes` rows minus the file's old contribution
plus its fresh pass-1 nodes, and the `symtab` unions with the file's row
swapped. The pass-1/pass-2 code is the SAME code the whole-project run uses;
only the lookup structures differ, so resolution semantics match by
construction wherever the views are faithful — and the single-file
convergence oracle is the proof, not this docstring.

Post-edge passes: override widening is skipped exactly as sink mode skips it
(`replace_file` runs the proven store twin); entrypoint roles are applied from
the persisted unions. The caller feeds the result straight to
`Store.replace_file`.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from ..model import Edge, Node, NodeKind
from ..store import Store
from . import python as _py

_MODULE = NodeKind.MODULE.value
_CLASS = NodeKind.CLASS.value


class _NameView:
    """Duck-typed stand-in for `proj.by_name` / `proj.class_by_name`: `.get`,
    `in`, and key iteration, answered from the nodes table (excluding the
    re-extracted file's OLD rows) plus the file's fresh pass-1 overlay.
    Every lookup is one indexed query, memoised."""

    def __init__(self, store: Store, exclude_file: str,
                 overlay: dict[str, list[str]], *, kind: str | None = None,
                 module_alias: bool = False) -> None:
        self._store = store
        self._exclude = exclude_file
        self._overlay = overlay
        self._kind = kind
        self._module_alias = module_alias
        self._memo: dict[str, list[str]] = {}

    def get(self, name: str, default=None):
        got = self._lookup(name)
        return got if got else ([] if default is None else default)

    def __contains__(self, name: str) -> bool:
        return bool(self._lookup(name))

    def __iter__(self):
        # Key iteration (the getattr-dispatch scan): all distinct names, store
        # plus overlay. Bounded per dispatch site; rare enough to keep simple.
        seen = set(self._overlay)
        yield from self._overlay
        sql = "SELECT DISTINCT name FROM nodes WHERE file != ?"
        params: list[object] = [self._exclude]
        if self._kind:
            sql += " AND kind = ?"
            params.append(self._kind)
        for r in self._store.conn.execute(sql, params):
            if isinstance(r["name"], str) and r["name"] not in seen:
                yield r["name"]

    def _lookup(self, name: str) -> list[str]:
        if name in self._memo:
            return self._memo[name]
        try:
            sql = "SELECT id FROM nodes WHERE name = ? AND file != ?"
            params: list[object] = [name, self._exclude]
            if self._kind:
                sql += " AND kind = ?"
                params.append(self._kind)
            sql += " ORDER BY rowid"  # insertion order ~ the whole-project append order
            ids = [r["id"] for r in self._store.conn.execute(sql, params)]
            if self._module_alias and "." not in name:
                # _index aliases modules by their short name (`from pkg import sub`).
                # SQLite LIKE is case-INSENSITIVE for ASCII, which would invent a
                # candidate `_index`'s exact rsplit never produces (module `pkg.base`
                # for symbol `Base`) — re-check the suffix case-sensitively in Python.
                ids += [r["id"] for r in self._store.conn.execute(
                    """SELECT id, name FROM nodes WHERE kind = ? AND file != ?
                        AND name LIKE '%.' || ? ORDER BY rowid""",
                    (_MODULE, self._exclude, name))
                        if isinstance(r["name"], str)
                        and r["name"].endswith("." + name)]
        except Exception:  # noqa: BLE001 — unstorable name: no candidates
            ids = []
        ids += self._overlay.get(name, [])
        out = list(dict.fromkeys(ids))
        self._memo[name] = out
        return out


class _IdView:
    """`proj.ids` / `proj.module_ids` stand-in: membership only."""

    def __init__(self, store: Store, exclude_file: str, overlay: set[str],
                 *, modules_only: bool = False) -> None:
        self._store = store
        self._exclude = exclude_file
        self._overlay = overlay
        self._modules_only = modules_only
        self._memo: dict[str, bool] = {}

    def __contains__(self, node_id: str) -> bool:
        if node_id in self._overlay:
            return True
        hit = self._memo.get(node_id)
        if hit is not None:
            return hit
        try:
            if self._modules_only:
                # module_ids excludes ids SHARED with a non-module node (panel
                # R14A): `main.py::main` must stay callable.
                row = self._store.conn.execute(
                    """SELECT 1 FROM nodes WHERE id = ? AND file != ?
                        AND kind = ?
                        AND NOT EXISTS (SELECT 1 FROM nodes n2 WHERE n2.id = nodes.id
                                          AND n2.kind != ?) LIMIT 1""",
                    (node_id, self._exclude, _MODULE, _MODULE)).fetchone()
            else:
                row = self._store.conn.execute(
                    "SELECT 1 FROM nodes WHERE id = ? AND file != ? LIMIT 1",
                    (node_id, self._exclude)).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        self._memo[node_id] = row is not None
        return row is not None


class _ModuleQualView:
    """`proj.module_by_qual` stand-in: exact qualname -> module id, with the
    src-layout alias (`flake8.x` finding `src.flake8.x`)."""

    def __init__(self, store: Store, exclude_file: str,
                 overlay: dict[str, str], source_prefix: str) -> None:
        self._store = store
        self._exclude = exclude_file
        self._overlay = overlay
        self._prefix = source_prefix
        self._memo: dict[str, str | None] = {}

    def get(self, qualname: str, default=None):
        if qualname in self._overlay:
            return self._overlay[qualname]
        if qualname in self._memo:
            got = self._memo[qualname]
            return got if got is not None else default
        names = [qualname]
        if self._prefix and not qualname.startswith(self._prefix):
            names.append(self._prefix + qualname)  # the _index alias, reversed
        got = None
        try:
            for nm in names:
                row = self._store.conn.execute(
                    """SELECT id FROM nodes WHERE kind = ? AND name = ?
                        AND file != ? ORDER BY rowid LIMIT 1""",
                    (_MODULE, nm, self._exclude)).fetchone()
                if row:
                    got = row["id"]
                    break
        except Exception:  # noqa: BLE001
            got = None
        self._memo[qualname] = got
        return got if got is not None else default


def extract_single_file(store: Store, root: str | Path, rel: str,
                        ) -> tuple[list[Node], list[Edge], dict[str, set[str]]]:
    """Extract ONE Python file against the store-backed symbol tables. Returns
    (nodes, edges, symtab_contribution) shaped exactly for
    `Store.replace_file(rel, nodes, edges, symtab=contribution)`. Raises
    SyntaxError/OSError to the caller — deciding the fallback is the caller's
    policy, exactly like the watch loop's other gates."""
    root = Path(root)
    path = root / rel
    tree = ast.parse(path.read_text(encoding="utf-8"))

    proj = _py._Project(root=root)
    proj.source_prefix = store.get_meta("source_prefix") or ""
    try:
        proj.packages = set(json.loads(store.get_meta("packages") or "[]"))
    except (ValueError, TypeError):
        proj.packages = set()

    # Pass 1 on this file alone: its nodes + its four raw name-set contributions.
    _py._collect_defs(proj, rel, path, tree)
    contribution = {k: set(v) for k, v in proj.symtab.get(rel, {}).items()}

    # The fresh nodes' overlay, mirroring `_index` exactly (short-name module
    # aliases, shared-id module exclusion) — but only over THIS file's nodes;
    # everything else answers from the store.
    ov_by_name: dict[str, list[str]] = {}
    ov_class: dict[str, list[str]] = {}
    ov_ids: set[str] = set()
    ov_mod_qual: dict[str, str] = {}
    ov_module_ids: set[str] = set()
    nonmodule: set[str] = set()
    for n in proj.nodes:
        ov_by_name.setdefault(n.name, []).append(n.id)
        ov_ids.add(n.id)
        if n.kind == NodeKind.CLASS:
            ov_class.setdefault(n.name, []).append(n.id)
        if n.kind == NodeKind.MODULE:
            ov_mod_qual[n.name] = n.id
            if proj.source_prefix and n.name.startswith(proj.source_prefix):
                ov_mod_qual.setdefault(n.name[len(proj.source_prefix):], n.id)
            ov_module_ids.add(n.id)
            if "." in n.name:
                ov_by_name.setdefault(n.name.rsplit(".", 1)[-1], []).append(n.id)
        else:
            nonmodule.add(n.id)
    ov_module_ids -= nonmodule

    proj.by_name = _NameView(store, rel, ov_by_name, module_alias=True)  # type: ignore[assignment]
    proj.class_by_name = _NameView(store, rel, ov_class, kind=_CLASS)  # type: ignore[assignment]
    proj.ids = _IdView(store, rel, ov_ids)  # type: ignore[assignment]
    proj.module_ids = _IdView(store, rel, ov_module_ids, modules_only=True)  # type: ignore[assignment]
    proj.module_by_qual = _ModuleQualView(store, rel, ov_mod_qual,  # type: ignore[assignment]
                                          proj.source_prefix)
    # The four name-set unions with this file's contribution swapped in (small;
    # materialised eagerly).
    for kind, attr in _py._SYM_KINDS.items():
        union = store.symtab_names(kind, exclude_file=rel)
        union |= contribution.get(kind, set())
        setattr(proj, attr, union)

    # Pass 2 on this file alone — the exact whole-project code over the views.
    _py._collect_edges(proj, rel, tree)

    # Entry-point roles for THIS file's nodes from the swapped unions. The
    # cross-file direction (this file's changed __all__ re-tagging OTHER files'
    # nodes) is the caller's job via replace_file(exported_ids=...).
    _py._apply_entrypoint_roles(proj)

    # Override widening is deliberately skipped, exactly like sink mode:
    # replace_file runs Store._propagate_overrides — the pinned DB twin.
    return proj.nodes, proj.edges, contribution
