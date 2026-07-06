"""Entry-point detection (design §4, Entry-point detection contract).

The linchpin: dead-code/hole *liveness* is entirely bounded by the entry-point
set, and no static detector catches every dynamic root — so a user override
(`stitchgraph.toml [entry_points]`) always augments whatever a detector finds.

A detector implements `detect(store) -> set[node_id]`. The first real detector
targets the Python **library + CLI** shape; for M0 it is a structural stub that
returns the override set plus any obvious roots already in the graph, and flags
that automatic detection is not yet wired so callers stay honest about it.
"""

from __future__ import annotations

from typing import Protocol

from .model import NodeKind
from .store import Store

# Languages whose files share PACKAGE scope (a directory) and run startup code together,
# with no per-file import edge to chain liveness — so module-node seeding is widened to the
# whole directory when any file in it is a root (panel R35A). Go is the clear case; add an
# extension here if another package-scoped language surfaces the same cardinal.
_PACKAGE_SCOPED_EXTS = (".go",)


def _is_package_scoped(file: str) -> bool:
    return file.endswith(_PACKAGE_SCOPED_EXTS)


def _dir_of(file: str) -> str:
    return file.rsplit("/", 1)[0] if "/" in file else ""


def _module_id_of(file: str) -> str:
    """The MODULE node id stitchgraph assigns a file: `{file}::{stem}` (stem = the filename
    without its final extension, matching pathlib.Path.stem). Used to seed the module-load
    root by id even when a same-stem top-level class/function clobbered the MODULE node."""
    name = file.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    stem = name[:dot] if dot > 0 else name  # dot>0: a leading-dot name has no suffix
    return f"{file}::{stem}"


class EntryPointDetector(Protocol):
    def detect(self, store: Store) -> set[str]: ...


class ConfigOnlyDetector:
    """M0 placeholder: roots come only from the user override allowlist.

    This deliberately does NOT guess. Until the real Python detector lands,
    `find_stale` must report low confidence + needs_review, because an empty or
    partial entry set produces false 'dead' verdicts — the dangerous failure.
    """

    not_implemented = True

    def __init__(self, overrides: set[str] | None = None) -> None:
        self.overrides = overrides or set()

    def detect(self, store: Store) -> set[str]:
        # Only return overrides that actually exist as nodes.
        return {nid for nid in self.overrides if store.get_node(nid) is not None}


class PythonLibraryDetector:
    """Real detector for the Python library + CLI shape (design §4).

    Roots = the export surface (public API) ∪ __main__ modules ∪ console-script
    targets ∪ tests ∪ user overrides. Critically, **exported public symbols are
    roots** — a library's public API is called by unknown external code, so it is
    never dead for lack of internal callers.

    Role tags are recorded by the extractor (exported / main / script / test);
    this detector applies policy over them.
    """

    not_implemented = False

    def __init__(self, overrides: set[str] | None = None, *,
                 include_tests: bool = True,
                 root_modules: list[str] | None = None) -> None:
        self.overrides = overrides or set()
        self.include_tests = include_tests
        self.root_modules = root_modules or []

    def detect(self, store: Store) -> set[str]:
        roots: set[str] = set()
        # 'runtime' = observed executing in a trace -> definitely live (design §2c);
        # 'callback' = a framework-invoked override (design §7).
        for role in ("exported", "main", "script", "route", "runtime", "callback"):
            roots.update(n.id for n in store.nodes_with_role(role))
        if self.include_tests:
            roots.update(n.id for n in store.nodes_with_role("test"))
        # HTTP routes / endpoints are entry points (external callers). TEMPLATE
        # nodes too (v3.39.0): a template is rendered BY NAME by the framework
        # (`render(request, "edit_inline/tabular.html")`), so the properties it
        # references ({{ obj.prop }} — the django-template resolver's edges) are
        # live even when no in-tree call chain reaches the template — the same
        # external-invocation logic as routes. Cardinal-safe: only ever adds roots.
        for kind in (NodeKind.ROUTE, NodeKind.ENDPOINT, NodeKind.TEMPLATE):
            roots.update(n.id for n in store.nodes_by_kind(kind))
        roots.update(nid for nid in self.overrides if store.get_node(nid) is not None)
        # Framework-loaded module trees (`[entry_points] root_modules` globs): modules a
        # plugin loader imports dynamically by name have no static importer, so their
        # module-level wiring — schema validators, registered hooks, dispatch-dict
        # entries — would be flagged dead. Rooting the MODULE node (not every symbol in
        # the file) keeps the analysis meaningful: only what the module body actually
        # references becomes live. Proven on Home Assistant's components/ tree, where it
        # rescued exactly the 33 module-level-rooted candidates (field analysis 2026-07-03).
        if self.root_modules:
            from fnmatch import fnmatch
            for m in store.nodes_by_kind(NodeKind.MODULE):
                mf = m.id.split("::", 1)[0]
                if any(fnmatch(mf, g) for g in self.root_modules):
                    roots.add(m.id)
        # A module's top-level code runs when the module is loaded, and the module is
        # loaded whenever any symbol it defines is reached (you can't call an exported
        # function or import a name without executing the module body). So a module that
        # owns any root is itself a load root: its module-level uses — registries, dispatch
        # tables, instantiations — then propagate liveness, instead of live code used only
        # at module scope being flagged dead (panel R12, cardinal). Module nodes are not
        # dead-code candidates, so seeding them never introduces a false dead.
        root_files = {rid.split("::", 1)[0] for rid in roots}
        # Package-scoped languages (Go): all files in a package (a directory) compile and run
        # as a unit — package-level `var` initializers and `init()` execute at startup for
        # EVERY file once the package is loaded, with no per-file import edge to chain
        # liveness (unlike Python, where an import edge loads a module on demand). So a
        # rootless package file whose module-level code is live (a registration side effect)
        # must be seeded when any SIBLING file in its directory is a root, or its functions
        # are flagged dead (panel R35A, cardinal). Gated to package-scoped extensions so
        # ordinary per-file-import languages aren't over-rooted.
        root_dirs = {_dir_of(f) for f in root_files if _is_package_scoped(f)}
        for m in store.nodes_by_kind(NodeKind.MODULE):
            mf = m.id.split("::", 1)[0]
            if mf in root_files or (_is_package_scoped(mf) and _dir_of(mf) in root_dirs):
                roots.add(m.id)  # existing MODULE node (handles __init__ package-name ids, Go)
        # Collision case: a top-level class/function sharing the file stem clobbers the MODULE
        # node `{file}::{stem}` into a symbol node, so nodes_by_kind(MODULE) misses it and the
        # module-level use edges (src = that id) lose their load-root — live module-load code is
        # flagged dead (panel R37A, cardinal). Seed that id directly for any root-owning file;
        # it's a no-op when the MODULE node survived (already seeded above) or when the computed
        # id doesn't exist (e.g. an __init__ whose module id is the package name, not the stem).
        for f in root_files:
            mid = _module_id_of(f)
            if store.get_node(mid) is not None:
                roots.add(mid)
        return roots


# Roots a Python library+CLI detector collects (design §4):
#   - public API: __all__ / __init__ exports  (NEVER flag these dead)
#   - [project.scripts] / [project.entry-points]
#   - if __name__ == "__main__"
#   - tests (pytest collection)
PYTHON_LIBRARY_ROOT_KINDS = (NodeKind.TEST, NodeKind.ENDPOINT, NodeKind.HANDLER)
