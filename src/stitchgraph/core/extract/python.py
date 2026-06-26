"""Python extractor (stdlib `ast`) — design §0, the M0 'your stack' extractor.

Produces nodes (Module / Class / Function / Method / Test) and edges
(CALLS / IMPORTS / INHERITS) in the shared ontology, plus entry-point role tags
(exported / main / script / test) for the detector to interpret.

Resolution is project-scoped and biased for **precision over recall** (design
§13.1 — never flag live code as dead):

- A reference resolving to exactly one project symbol -> a confident edge.
- A reference matching several symbols (same name, e.g. an overridden method)
  -> AMBIGUOUS edges to *all* candidates, so reachability over-approximates and
  never wrongly calls a live symbol dead.
- A reference matching nothing internal (builtins / third-party) is dropped as
  external, not flagged as a hole. Holes come from *internal imports* that don't
  resolve, which is the reliable signal without a type system (that's the LSP's
  job — design §5).

tree-sitter + an LSP are the documented upgrade for incremental reparse, error
tolerance, polyglot coverage, and live types; the contract (path -> nodes+edges)
is identical, so they swap in without touching the store or operations.
"""

from __future__ import annotations

import ast
import builtins
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from ._testfile import is_test_file as _is_test_file

_BUILTINS = set(dir(builtins))


@dataclass
class _Project:
    root: Path
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    by_name: dict[str, list[str]] = field(default_factory=dict)
    class_by_name: dict[str, list[str]] = field(default_factory=dict)
    ids: set[str] = field(default_factory=set)
    packages: set[str] = field(default_factory=set)
    exported_names: set[str] = field(default_factory=set)
    main_calls: set[str] = field(default_factory=set)
    module_consts: set[str] = field(default_factory=set)  # module-level assigned names
    external_base_classes: set[str] = field(default_factory=set)  # subclass framework bases
    module_by_qual: dict[str, str] = field(default_factory=dict)  # module qualname -> node id
    module_ids: set[str] = field(default_factory=set)  # all MODULE node ids
    source_prefix: str = ""  # qualname prefix of a src-layout source root, e.g. "src." (else "")

# Ordinary bases whose subclasses are NOT framework callbacks — their methods
# should still be eligible for dead-code. Anything else external (HTMLParser,
# threading.Thread, a web View, …) is treated as a framework base.
_PLAIN_BASES = {
    "object", "Exception", "BaseException", "ValueError", "RuntimeError",
    "TypeError", "KeyError", "Protocol", "ABC", "ABCMeta", "Enum", "IntEnum",
    "Flag", "str", "int", "dict", "list", "tuple", "set", "frozenset",
    "NamedTuple", "TypedDict", "Generic", "Iterator", "Iterable",
}


def extract_project(root: str | Path,
                    ignore: list[str] | None = None) -> tuple[list[Node], list[Edge]]:
    """Two passes: (1) collect definitions + symbol table, (2) resolve references.

    `ignore` is a list of globs (relative to root) to skip — e.g. migrations.
    """
    proj = _Project(root=Path(root))
    try:
        files = sorted(p for p in proj.root.rglob("*.py")
                       if p.is_file() and _wanted(p, proj.root)
                       and not _ignored(p, proj.root, ignore))
    except OSError:
        files = []  # unwalkable root (over-long path / permission) -> empty extraction,
                    # not a crash; reindex degrades to 0 nodes like a missing path (panel YYY)
    proj.source_prefix = _detect_source_prefix(files, proj.root)
    proj.packages = _project_packages(files, proj.root, proj.source_prefix)

    parsed: dict[str, ast.Module] = {}
    for path in files:
        rel = path.relative_to(proj.root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            _collect_defs(proj, rel, path, tree)
        except (SyntaxError, UnicodeDecodeError, OSError, RecursionError):
            # Skip the one file, never abort the whole reindex (panel DDD/OOO).
            # OSError: a broken symlink / unreadable file (submodules, races).
            # RecursionError: a pathologically deep AST — a huge flat expression
            # (generated SQL/HTML/string builders) overflows ast.parse or the walk;
            # one bad file must not leave the entire DB empty.
            parsed.pop(rel, None)
            continue
        parsed[rel] = tree

    _index(proj)
    _apply_entrypoint_roles(proj)
    _apply_script_roles(proj)
    _seed_entrypoint_classes(proj)
    for rel, tree in parsed.items():
        try:
            _collect_edges(proj, rel, tree)
        except RecursionError:
            continue  # same pathological-depth guard for the edge pass (panel OOO)
    _apply_callback_roles(proj)
    _seed_test_classes(proj)
    _seed_exported_inherited_methods(proj)
    _seed_protocol_dunders(proj)
    _propagate_overrides(proj)
    return proj.nodes, proj.edges


def _seed_test_classes(proj: _Project) -> None:
    """A class with a test member (a `test_*` method, recorded as `NodeKind.TEST`, or
    any member carrying the `test` role) is a test fixture — mark the class `test` so it
    isn't flagged dead while its methods are live (the 'method live, class dead' shape).
    Mirrors the tree-sitter `_seed_test_classes`. This is the dominant Python test layout
    (`class TestWidget:` / a `unittest.TestCase` with only `test_*` methods); the
    callback path above only rescued classes with a *non-test* override (e.g. `setUp`)."""
    class_ids = {n.id for n in proj.nodes if n.kind is NodeKind.CLASS}
    test_classes = {n.id.rsplit(".", 1)[0] for n in proj.nodes
                    if (n.kind is NodeKind.TEST or "test" in n.roles)
                    and n.id.rsplit(".", 1)[0] in class_ids}
    if not test_classes:
        return
    # Grow the seed set to a single combined fixed point over two axes: (a) enclosing
    # containers of a nested test class, and (b) transitive subclasses of a test base
    # (abstract-base + thin-subclass idiom; INHERITS edges are resolved child->base, as
    # _collect_edges ran first). They must co-iterate — a class found by inheritance may
    # itself need its enclosing chain walked, and vice versa (Panel BB finding 1).
    inh = [(e.src, e.dst_id) for e in proj.edges
           if e.relation is Relation.INHERITS and e.dst_id]

    def add_enclosing(cid: str) -> bool:
        rel, _, qual = cid.partition("::")
        segs = qual.split(".")
        added = False
        for i in range(1, len(segs)):
            anc = f"{rel}::{'.'.join(segs[:i])}"
            if anc in class_ids and anc not in test_classes:
                test_classes.add(anc)
                added = True
        return added

    changed = True
    while changed:
        changed = False
        for cid in list(test_classes):
            if add_enclosing(cid):
                changed = True
        for child_id, base_id in inh:
            if child_id not in test_classes and base_id in test_classes:
                test_classes.add(child_id)
                changed = True
    for n in proj.nodes:
        if n.id in test_classes:
            n.roles = n.roles | {"test"}


def _apply_callback_roles(proj: _Project) -> None:
    """Methods of a class with a framework base are likely framework-invoked
    overrides (e.g. HTMLParser.handle_starttag) — mark them 'callback' so they're
    roots, not dead-code false positives (design §7 caveat). The class itself is
    framework-instantiated too (e.g. a `unittest.TestCase` subclass, registered by a
    test runner), so mark it a root as well — otherwise the methods are live but the
    class is flagged dead (the 'method live, class dead' shape)."""
    class_ids = {n.id for n in proj.nodes if n.kind is NodeKind.CLASS}
    # Framework (externally-subclassed) classes whose methods are framework-invoked
    # overrides. Three signals, unioned:
    #  (a) the inline name heuristic (`external_base_classes`): a base name that is neither a
    #      first-party class nor a known stdlib plain base;
    #  (b) an INHERITS edge that did NOT resolve to a distinct first-party class — either
    #      unresolved (no dst_id) or a SELF-LOOP (dst_id == src), the same-leaf-name collision
    #      where `class EnvironBuilder(werkzeug.test.EnvironBuilder)` bound its base to itself
    #      (C2). Gated by `_PLAIN_BASES` on the base leaf so `class E(Exception)` stays plain;
    #  (c) transitive descent: a first-party subclass of a framework class is itself
    #      framework-driven (FlaskGroup -> AppGroup -> click.Group, C1) — its overrides are
    #      invoked the same way, so liveness must propagate down INHERITS.
    framework: set[str] = set(proj.external_base_classes)
    subclasses: dict[str, set[str]] = {}
    for e in proj.edges:
        if e.relation is not Relation.INHERITS or e.src not in class_ids:
            continue
        if e.dst_id and e.dst_id != e.src and e.dst_id in class_ids:
            subclasses.setdefault(e.dst_id, set()).add(e.src)  # resolved first-party base
        elif e.dst_symbol not in _PLAIN_BASES:
            framework.add(e.src)  # unresolved / self-loop external base (and not a plain base)
    if not framework:
        return
    stack = list(framework)
    while stack:  # (c) transitive closure down the INHERITS tree (cardinal-safe: only adds)
        cid = stack.pop()
        for sub in subclasses.get(cid, ()):
            if sub not in framework:
                framework.add(sub)
                stack.append(sub)
    classes_with_callbacks: set[str] = set()
    for node in proj.nodes:
        if node.kind is NodeKind.METHOD and "." in node.id:
            class_id = node.id.rsplit(".", 1)[0]
            if class_id in framework:
                node.roles = node.roles | {"callback"}
                classes_with_callbacks.add(class_id)
    # A framework subclass that actually overrides hook methods is framework-
    # instantiated (a `unittest.TestCase`, an `HTMLParser`); mark the class a root so
    # it isn't flagged dead while its methods are live. Tie this to *having* callback
    # methods, not merely to the base — a bare `class Meta(type): pass` metaclass that
    # is never used must still be flagged (it has no hook methods to override).
    for node in proj.nodes:
        if node.kind is NodeKind.CLASS and node.id in classes_with_callbacks:
            node.roles = node.roles | {"callback"}


def _seed_protocol_dunders(proj: _Project) -> None:
    """Tie each dunder method's liveness to its class. A dunder is invoked implicitly by
    the interpreter (`instance()` -> `__call__`; attribute access on a descriptor ->
    `__get__`/`__set__`; `obj[k]` -> `__getitem__`; `with obj` -> `__enter__`; etc.), so it
    has no explicit call site — a helper it alone calls is orphaned and confidently flagged
    dead once the class is in use (panel R20A, cardinal). Add a REFERENCES edge class ->
    dunder so that when the class is reachable, its dunders (and their callees) are too.

    Scoped to the class: a dead class's dunders stay dead (no over-rooting). Dunders are
    already excluded from stale candidates, so this only rescues their *callees*."""
    class_ids = {cid for ids in proj.class_by_name.values() for cid in ids}
    for node in proj.nodes:
        name = node.name
        if (node.kind is NodeKind.METHOD and "." in node.id
                and len(name) > 4 and name.startswith("__") and name.endswith("__")):
            class_id = node.id.rsplit(".", 1)[0]
            if class_id in class_ids:
                proj.edges.append(Edge(
                    src=class_id, relation=Relation.REFERENCES, dst_symbol=name,
                    dst_id=node.id, weight=1.0, provenance=Provenance.INFERRED,
                    location=node.location, source="ast"))


def _propagate_overrides(proj: _Project) -> None:
    """Polymorphic dispatch: a CALLS or REFERENCES edge bound to a base-class member must
    also reach overriding members in subclasses, or a live override gets no inbound edge
    and is flagged dead (CARDINAL). The precision paths bind `self.m()` / `self.prop` to
    the *enclosing* class and `var.m()` / `var.prop` (var annotated `Base`/`Protocol`/`ABC`)
    to the *declared* class — neither widens to the concrete subclass that runs at runtime.
    Mirror the unknown-receiver widening by adding AMBIGUOUS edges (same relation) from the
    same source to every subclass override of the bound member. REFERENCES is included so a
    property/attribute read on `self` whose subclass overrides it is not flagged dead (the
    read-side twin of the call case, panel R21A).

    Adding edges can only make more nodes reachable, never fewer, so this is cardinal-
    safe by construction; the cost is mild over-approximation (a genuinely-dead override
    of a used base member stays live), which is the documented lower-severity trade-off.
    """
    _WIDENED = (Relation.CALLS, Relation.REFERENCES)
    class_ids = {cid for ids in proj.class_by_name.values() for cid in ids}
    if not class_ids:
        return
    # direct subclass map: base_class_id -> {subclass_id, ...} (project classes only).
    subclasses: dict[str, set[str]] = {}
    for e in proj.edges:
        if (e.relation is Relation.INHERITS and e.src in class_ids
                and e.dst_id in class_ids and e.src != e.dst_id):
            subclasses.setdefault(e.dst_id, set()).add(e.src)
    if not subclasses:
        return
    cache: dict[str, set[str]] = {}

    def descendants(base_id: str) -> set[str]:
        if base_id in cache:
            return cache[base_id]
        out: set[str] = set()
        stack = list(subclasses.get(base_id, ()))
        while stack:
            s = stack.pop()
            if s in out:
                continue
            out.add(s)
            stack.extend(subclasses.get(s, ()))
        cache[base_id] = out
        return out

    # Never duplicate a (source, relation, target) edge we already emitted.
    seen = {(e.src, e.relation, e.dst_id) for e in proj.edges if e.relation in _WIDENED}
    new_edges: list[Edge] = []
    for e in list(proj.edges):
        if e.relation not in _WIDENED or not e.dst_id:
            continue
        base_id, sep, method = e.dst_id.rpartition(".")
        if not sep or base_id not in class_ids:
            continue
        for sub_id in descendants(base_id):
            override = f"{sub_id}.{method}"
            if override == e.dst_id or override not in proj.ids:
                continue
            if (e.src, e.relation, override) in seen:
                continue
            seen.add((e.src, e.relation, override))
            new_edges.append(Edge(
                src=e.src, relation=e.relation, dst_symbol=method,
                dst_id=override, weight=1.0, provenance=Provenance.AMBIGUOUS,
                location=e.location, source="ast"))
    proj.edges.extend(new_edges)


def _seed_exported_inherited_methods(proj: _Project) -> None:
    """Public methods a class *inherits* from a first-party base are part of its public
    surface too. `_apply_entrypoint_roles` roots the public methods defined directly on an
    exported class, but a method defined on a (non-exported) base class — `Flask(App)`,
    `App(Scaffold)`, where `.shell_context_processor`/`.patch` live on the base and are
    called on a `Flask` instance — was flagged dead for lack of an internal caller (a
    cardinal-class false positive surfaced by the flask corpus).

    So: for every exported class, walk its INHERITS ancestor chain and root the public
    (non-underscore) methods of each first-party ancestor. INHERITS edges are resolved
    child->base and only exist after _collect_edges, so this must run in the post-edge phase.
    Only ever adds roots (cardinal-safe); over-rooting a shadowed base method is the same
    documented precision-over-recall trade-off as the exported-class rule itself."""
    exported_class_ids = {n.id for n in proj.nodes
                          if n.kind is NodeKind.CLASS and n.name in proj.exported_names}
    if not exported_class_ids:
        return
    class_ids = {cid for ids in proj.class_by_name.values() for cid in ids}
    # child_id -> {base_id, ...}, first-party classes only (dst_id set ⟹ resolved internal).
    bases: dict[str, set[str]] = {}
    for e in proj.edges:
        if (e.relation is Relation.INHERITS and e.src in class_ids
                and e.dst_id in class_ids and e.src != e.dst_id):
            bases.setdefault(e.src, set()).add(e.dst_id)
    if not bases:
        return
    # Transitive ancestor closure of all exported classes (the bases whose public methods
    # become public API). Excludes the exported classes themselves — those are handled by
    # the direct-method rule already.
    ancestors: set[str] = set()
    stack = [b for cid in exported_class_ids for b in bases.get(cid, ())]
    while stack:
        a = stack.pop()
        if a in ancestors:
            continue
        ancestors.add(a)
        stack.extend(bases.get(a, ()))
    if not ancestors:
        return
    for node in proj.nodes:
        if node.kind is NodeKind.METHOD and "." in node.id \
                and not node.name.startswith("_") \
                and node.id.rsplit(".", 1)[0] in ancestors:
            node.roles = node.roles | {"exported"}


# -- pass 1: definitions ----------------------------------------------------
def _collect_defs(proj: _Project, rel: str, path: Path, tree: ast.Module) -> None:
    is_init = path.name == "__init__.py"
    # Directory-aware (shared with the tree-sitter extractor) so a `test_*` method in a
    # shared base under `tests/`/`conftest.py` is recognised — otherwise a thin subclass
    # inheriting those tests is flagged dead (Panel CC).
    is_test_file = _is_test_file(rel)
    module_qual = _module_qualname(rel)
    mod_id = Node.make_id(rel, module_qual)

    exported = _dunder_all(tree)
    if exported:
        proj.exported_names.update(exported)
    proj.main_calls.update(_main_block_calls(tree))
    for stmt in tree.body:  # module-level constants (not graphed as nodes)
        if isinstance(stmt, ast.Assign):
            proj.module_consts.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            proj.module_consts.add(stmt.target.id)
    has_main = _has_main_block(tree)

    proj.nodes.append(Node(
        id=mod_id, kind=NodeKind.MODULE, name=module_qual, location=f"{rel}:1:0",
        roles=frozenset({"main"} if has_main else set()),
    ))
    # Public names of a package __init__ are part of the export surface.
    if is_init:
        # Look THROUGH control-flow blocks (`try/except ImportError` for optional deps,
        # `if sys.version_info` backport branches), which don't create a scope — a re-export
        # nested there is still public API. Only top-level was scanned before, so a
        # conditional re-export's target was flagged dead (panel R26A, cardinal). Mirror
        # `_scope_defs`: recurse through control flow, never into a def/class body.
        for node in _module_export_nodes(tree):
            nm = getattr(node, "name", None)
            if nm and not nm.startswith("_"):
                proj.exported_names.add(nm)
            # Re-exports: `from .api import Public` in a package __init__ makes
            # `pkg.Public` importable public API — an export root even though it
            # isn't physically defined here. ast.ImportFrom/Import carry `.names`
            # aliases, not a `.name`, so the check above misses them and the
            # re-exported symbol is flagged dead (live public API as dead — the
            # cardinal sin). The bound public name is the asname or the leaf.
            elif isinstance(node, (ast.ImportFrom, ast.Import)):
                for alias in node.names:
                    if isinstance(node, ast.Import):
                        bound = alias.asname or alias.name.split(".")[0]
                    else:
                        bound = alias.asname or alias.name
                    if bound != "*" and not bound.startswith("_"):
                        proj.exported_names.add(bound)
                        # Under a RENAMED re-export (`from .core import Engine as Public`),
                        # the bound public name (`Public`) differs from the actually-defined
                        # symbol's name (`Engine`). `_apply_entrypoint_roles` matches nodes by
                        # their defined name, so also register the original leaf — gated on
                        # the *bound* name being public — or the renamed re-export's target
                        # (and its methods) is flagged dead (panel R25A, cardinal). A private
                        # bound name (`import _hidden`) is skipped above, staying dead.
                        if isinstance(node, ast.ImportFrom):
                            proj.exported_names.add(alias.name)
            # Alias re-export by assignment: `Public = impl.Thing` / `Public = _Internal`
            # in a package __init__ exposes the RHS symbol as public API under `Public`.
            # The scan above only sees defs/imports, so the aliased target was flagged dead
            # (panel R26B). Gated on the assigned (public) name; root the RHS's referenced
            # symbol (a bare Name or the leaf of an attribute like `impl.Thing`).
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                if any(isinstance(t, ast.Name) and not t.id.startswith("_")
                       for t in targets):
                    ref = _assign_rhs_name(node.value)
                    if ref:
                        proj.exported_names.add(ref)

    for node in _scope_defs(tree):
        _def_node(proj, rel, node, parent="", is_test_file=is_test_file)


def _scope_defs(scope: ast.AST) -> list[ast.AST]:
    """Function/class defs that belong to `scope`'s own namespace — looking *through*
    control-flow blocks (`if`/`elif`/`else`/`for`/`while`/`try`/`except`/`finally`/
    `with`/`match`), which do NOT create a scope in Python, but NOT crossing into a
    nested def/class (each owns its own nested defs). So in
    `def f():\n    if c:\n        def g(): ...` this yields `g` for f's scope, at qual
    `f.g` (control flow adds no qual level). Without it, a def nested in a control-flow
    block is never modeled as a node, yet `_walk_scope` (which walks the same way) emits
    edges from its qualname — a phantom source that can't reach, so a symbol used only
    there is flagged dead (live code as dead — the cardinal sin). `_def_node` and
    `_walk_scope` MUST traverse identically so edge-source ids line up with node ids."""
    out: list[ast.AST] = []

    def rec(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.append(child)  # a scope-level def; its own nested defs recurse later
            else:
                rec(child)         # look through control flow (and inert expressions)

    rec(scope)
    return out


def _module_export_nodes(tree: ast.Module) -> list[ast.AST]:
    """Module-scope defs/classes AND imports of a package `__init__`, looking *through*
    control-flow blocks (`try/except`, `if/else`) but never into a def/class body. Used to
    scan the export surface so a re-export nested in an optional-dependency `try/except` or
    a version-backport `if` is still recognized as public API (panel R26A)."""
    out: list[ast.AST] = []

    # Distinct name (not the shared `rec` of the other walkers) so this helper doesn't join
    # the same-named inner-helper cluster that the documented fan_in-fallback note covers.
    def _descend(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                  ast.ImportFrom, ast.Import, ast.Assign, ast.AnnAssign)):
                out.append(child)   # module-scope def/class/import/alias; don't descend
            else:
                _descend(child)     # look through control flow
    _descend(tree)
    return out


def _assign_rhs_name(value: ast.AST | None) -> str | None:
    """The project symbol an alias assignment's RHS refers to: `Public = Thing` -> "Thing";
    `Public = impl.Thing` -> "Thing" (attribute leaf). None for calls/constants/other."""
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _def_node(proj: _Project, rel: str, node: ast.AST, parent: str,
              is_test_file: bool, in_abstract: bool = False,
              parent_is_class: bool = False) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        qual = f"{parent}.{node.name}" if parent else node.name
        kind = NodeKind.METHOD if parent_is_class else NodeKind.FUNCTION
        roles: set[str] = set()
        if is_test_file and node.name.startswith("test"):
            roles.add("test")
            kind = NodeKind.TEST
        # An empty body inside a Protocol/ABC or under @abstractmethod is an
        # intentional contract, not an implementation hole (design §7 caveat).
        is_stub = _is_stub(node) and not _is_abstract(node, in_abstract)
        proj.nodes.append(Node(
            id=Node.make_id(rel, qual), kind=kind, name=node.name,
            location=f"{rel}:{node.lineno}:{node.col_offset}",
            end_line=getattr(node, "end_lineno", None),
            is_stub=is_stub, arity=_arity(node), roles=frozenset(roles),
            summary=_docstring(node),
        ))
        # Descend into the body so function-local classes/functions become real
        # nodes. _walk_scope already emits edges from their qualnames (e.g.
        # `run.Local.helper`); without a node at that id those edges have a phantom
        # src and never participate in reachability, so a symbol used only inside a
        # function-local class/closure is flagged dead (live code as dead — the
        # cardinal sin; tree-sitter already models nested defs). `_scope_defs` looks
        # through control-flow blocks (a def in an `if`/`for`/`try`), matching
        # _walk_scope, so the two stay aligned (qual = enclosing scope, no block level).
        for inner in _scope_defs(node):
            _def_node(proj, rel, inner, parent=qual, is_test_file=is_test_file)
    elif isinstance(node, ast.ClassDef):
        qual = f"{parent}.{node.name}" if parent else node.name
        proj.nodes.append(Node(
            id=Node.make_id(rel, qual), kind=NodeKind.CLASS, name=node.name,
            location=f"{rel}:{node.lineno}:{node.col_offset}",
            end_line=getattr(node, "end_lineno", None), summary=_docstring(node),
        ))
        abstract = _is_abstract_class(node)
        for child in _scope_defs(node):
            _def_node(proj, rel, child, parent=qual, is_test_file=is_test_file,
                      in_abstract=abstract, parent_is_class=True)


def _index(proj: _Project) -> None:
    nonmodule_ids: set[str] = set()
    for n in proj.nodes:
        proj.by_name.setdefault(n.name, []).append(n.id)
        proj.ids.add(n.id)
        if n.kind == NodeKind.CLASS:
            proj.class_by_name.setdefault(n.name, []).append(n.id)
        # Alias modules by their short name so `from pkg import submodule` resolves.
        if n.kind == NodeKind.MODULE:
            proj.module_by_qual[n.name] = n.id  # exact qualname -> module node (panel R13)
            # src-layout: also key the module by its src-stripped qualname (`src.flake8.x` ->
            # `flake8.x`), the name absolute imports actually use, so `from flake8 import x`
            # module-load edges resolve. Node ids/qualnames are left untouched (the resolver
            # pipeline rebuilds ids from the path), so only the lookup gains an alias.
            if proj.source_prefix and n.name.startswith(proj.source_prefix):
                proj.module_by_qual.setdefault(n.name[len(proj.source_prefix):], n.id)
            proj.module_ids.add(n.id)
            if "." in n.name:
                proj.by_name.setdefault(n.name.rsplit(".", 1)[-1], []).append(n.id)
        else:
            nonmodule_ids.add(n.id)
    # A root-level `utils.py` defining `def utils()` gives the MODULE node and the FUNCTION
    # node the SAME id (`utils.py::utils`). Keep such a shared id OUT of module_ids so the
    # call-resolution filter in _ref_edges doesn't drop a real function call (the near-
    # universal `main.py` + `def main()` pattern) — panel R14A, cardinal.
    proj.module_ids -= nonmodule_ids


def _apply_entrypoint_roles(proj: _Project) -> None:
    """Mark roots once the whole symbol table is known: exported names (public
    API, incl. re-exports) and functions invoked from `__main__` blocks."""
    # Public methods of an exported class are themselves public API: external code
    # holding an instance can call them, so they are never dead for lack of an
    # internal caller (precision over recall). Underscore-prefixed methods stay
    # private — reached only if something internal calls them.
    exported_class_ids = {n.id for n in proj.nodes
                          if n.kind is NodeKind.CLASS and n.name in proj.exported_names}
    for node in proj.nodes:
        extra: set[str] = set()
        if node.name in proj.exported_names and node.kind in (
                NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
            extra.add("exported")
        if node.kind is NodeKind.METHOD and "." in node.id \
                and not node.name.startswith("_") \
                and node.id.rsplit(".", 1)[0] in exported_class_ids:
            extra.add("exported")
        # A class instantiated in a `__main__` block (`Worker().run()`) is a live entry
        # root just like a called function — include CLASS, else the class (and its
        # methods, rescued below) is false-flagged dead (panel DDD, cardinal).
        if node.name in proj.main_calls and node.kind in (NodeKind.FUNCTION, NodeKind.CLASS):
            extra.add("main")
        if extra:
            node.roles = node.roles | extra


def _seed_entrypoint_classes(proj: _Project) -> None:
    """Keep a class live when an entry point targets one of its methods (the 'method
    live, class dead' cardinal shape — panel DDD). Runs after all role assignment.

    (1) A **`script`** root on a method (a console-script `Class.method` target, which is
        module-path-matched and so collision-resistant) keeps its enclosing class chain
        live (`App.run` -> `App`; `Widget.Inner.go` -> `Widget.Inner` and `Widget`).
    (2) A class instantiated in a `__main__` block (it carries the `main` role from
        `_apply_entrypoint_roles`) keeps the methods it *invokes* live — those whose name
        appears in the same `main_calls` set (e.g. `Worker().run()` -> `run`).

    Deliberately narrow (panel EEE): step (1) is restricted to the `script` role, and
    step (2) to invoked method names — NOT every public method of every root-bearing
    class — so a global bare-name collision (`exported`/`main` match by name across
    modules) can't drag a whole unrelated class + its full method surface live. Only
    ever adds roots (precision-safe — never flags live code dead)."""
    by_id = {n.id: n for n in proj.nodes}
    # Snapshot classes that are DIRECT entry-point targets (`pkg:SomeClass`) BEFORE step (1)
    # runs — at this point only `_apply_script_roles` has applied the `script` role, so a
    # CLASS carrying it is a direct target. Step (1) below also adds `script` to classes that
    # merely *enclose* a `Class.method` target, so step (3) must NOT key off the post-step-(1)
    # set or it would root the whole public surface of every CLI's command class (panel R40A).
    direct_script_class_ids = {n.id for n in proj.nodes
                               if n.kind is NodeKind.CLASS and "script" in n.roles}
    # (1) class(es) enclosing a `script`-rooted method (module-precise).
    for n in proj.nodes:
        if n.kind not in (NodeKind.METHOD, NodeKind.FUNCTION) or "script" not in n.roles:
            continue
        cid = n.id
        while "::" in cid and "." in cid.split("::", 1)[1]:
            cid = cid.rsplit(".", 1)[0]
            owner = by_id.get(cid)
            if owner is not None and owner.kind is NodeKind.CLASS:
                owner.roles = owner.roles | {"script"}
    # (2) invoked methods of a class instantiated in a `__main__` block.
    main_class_ids = {n.id for n in proj.nodes
                      if n.kind is NodeKind.CLASS and "main" in n.roles}
    if main_class_ids:
        for n in proj.nodes:
            if n.kind is NodeKind.METHOD and not n.name.startswith("_") \
                    and n.name in proj.main_calls \
                    and n.id.rsplit(".", 1)[0] in main_class_ids:
                n.roles = n.roles | {"main"}
    # (3) public methods of a class that is itself a DIRECT `script`-rooted entry-point target
    # (a plugin class, e.g. `flake8.report = default = ...:Default`). The framework
    # instantiates it and calls its protocol methods, none of which has an internal caller —
    # so they are live API exactly like the public methods of an exported class. Keyed off the
    # PRE-step-(1) snapshot of *direct* targets, never the enclosing classes step (1) added, so
    # a plain `Class.method` CLI entry point doesn't root its class's whole public surface
    # (panel R40A). The class target is module-path-precise (matched in _apply_script_roles),
    # so this can't drag an unrelated same-named class live. Only ever adds roots
    # (cardinal-safe). Underscore methods stay private — reached only if something calls them.
    if direct_script_class_ids:
        for n in proj.nodes:
            if n.kind is NodeKind.METHOD and not n.name.startswith("_") \
                    and n.id.rsplit(".", 1)[0] in direct_script_class_ids:
                n.roles = n.roles | {"script"}


def _spec_to_candidates(spec: str) -> list[tuple[str, str]]:
    """Turn one entry-point spec (`"pkg.mod:obj"`, optionally `"... [extra]"`) into the
    (module_path_suffix, object_leaf) candidates we tag. The module may be a plain module
    (`pkg/mod.py`) OR a package whose target lives in its `__init__.py` (`pkg:main` ->
    `pkg/__init__.py`) — emit both, harmless because each only matches a node that actually
    exists at that path (panel ZZ, cardinal-class)."""
    if ":" not in spec:
        return []
    module, _, obj = spec.partition(":")
    module = module.strip()
    obj = obj.split("[", 1)[0].strip()  # drop any "[extra]" suffix
    if not (module and obj):
        return []
    base = module.replace(".", "/")
    return [(base + ".py", obj), (base + "/__init__.py", obj)]


def _pyproject_targets(root: Path) -> list[tuple[str, str]]:
    """Parse pyproject.toml for console/GUI/plugin entry points (design §4, issue #21).

    Returns (module_path_suffix, object_name) pairs from `[project.scripts]`,
    `[project.gui-scripts]`, and every `[project.entry-points.*]` group."""
    pp = root / "pyproject.toml"
    if not pp.is_file():
        return []  # is_file() (not exists()) so a FIFO/dir named pyproject.toml is
                   # skipped: read_text() would open a FIFO and block forever, and the
                   # OSError guard below never fires on a blocking open (panel JJJ)
    import tomllib
    try:
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return []  # malformed pyproject -> no roots, never a crash
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    tables: list[dict] = []
    for key in ("scripts", "gui-scripts"):
        tbl = project.get(key)
        if isinstance(tbl, dict):
            tables.append(tbl)
    eps = project.get("entry-points")
    if isinstance(eps, dict):
        tables.extend(g for g in eps.values() if isinstance(g, dict))
    out: list[tuple[str, str]] = []
    for tbl in tables:
        for spec in tbl.values():
            if isinstance(spec, str):
                out.extend(_spec_to_candidates(spec))
    return out


def _setup_cfg_targets(root: Path) -> list[tuple[str, str]]:
    """Parse setup.cfg `[options.entry_points]` for console/GUI/plugin entry points.

    The older-but-still-ubiquitous packaging format (flake8, isort, … declare their
    plugins here). Every group — `console_scripts`, `gui_scripts`, AND plugin groups like
    `flake8.extension`/`flake8.report` — registers code loaded only via the entry-point
    machinery, so its targets are live roots exactly like the pyproject ones. Without this,
    a plugin function/class with no internal caller is flagged dead (cardinal-class FP)."""
    sc = root / "setup.cfg"
    if not sc.is_file():
        return []  # is_file() (not exists()): a FIFO/dir named setup.cfg would block read
    import configparser
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(sc.read_text(encoding="utf-8"))
    except (configparser.Error, OSError, UnicodeDecodeError):
        return []  # malformed setup.cfg -> no roots, never a crash
    if not parser.has_section("options.entry_points"):
        return []
    out: list[tuple[str, str]] = []
    # Each key is a group name; its value is a multiline block of `name = pkg.mod:obj`
    # lines (configparser folds the indented continuation lines into one string).
    for value in parser["options.entry_points"].values():
        if not value:
            continue
        for line in value.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            _, _, spec = line.partition("=")  # drop the `name =` label, keep `pkg.mod:obj`
            out.extend(_spec_to_candidates(spec.strip()))
    return out


def _console_script_targets(root: Path) -> list[tuple[str, str]]:
    """All entry-point targets declared for the project, across both packaging formats:
    pyproject.toml `[project.*]` and setup.cfg `[options.entry_points]`. A spec is
    `"pkg.mod:func"`; the module becomes a path suffix (`pkg/mod.py`) and the object's
    leaf name is what we tag. (setup.py `entry_points=` is arbitrary Python, not statically
    parseable, and is left to the user override — see LIMITATIONS.)"""
    return _pyproject_targets(root) + _setup_cfg_targets(root)


def _apply_script_roles(proj: _Project) -> None:
    """Tag console-script / entry-point targets with role `script` so a CLI's `main`
    (the product, not dead code) isn't flagged stale for lack of an internal caller
    (issue #21). Matched by object leaf-name AND module path suffix, so a same-named
    function in an unrelated module isn't mis-rooted (precision over recall)."""
    targets = _console_script_targets(proj.root)
    if not targets:
        return
    by_name: dict[str, list[Node]] = {}
    for n in proj.nodes:
        # CLASS included: a plugin entry point can target a class
        # (`flake8.extension = F = flake8.plugins.pyflakes:FlakesChecker`), instantiated and
        # driven by the framework — its public methods are rescued in _seed_entrypoint_classes.
        if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
            by_name.setdefault(n.name, []).append(n)
    for mod_suffix, obj in targets:
        leaf = obj.split(".")[-1]  # "Class.method"/"func" -> the node's own name
        for n in by_name.get(leaf, []):
            filepart, _, qual = n.id.partition("::")
            if not (filepart == mod_suffix or filepart.endswith("/" + mod_suffix)):
                continue
            # A `Class.method` target names a specific method — require the node's full
            # qualified name to match, so a same-named method on a *different* class in
            # the same file isn't also rooted (panel WW). Bare `func` targets match by
            # leaf as before.
            if "." in obj and qual != obj:
                continue
            n.roles = n.roles | {"script"}


# -- pass 2: edges ----------------------------------------------------------
def _collect_edges(proj: _Project, rel: str, tree: ast.Module) -> None:
    module_qual = _module_qualname(rel)
    mod_id = Node.make_id(rel, module_qual)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _import_edge(proj, mod_id, rel, alias.name, node.lineno)
                _module_load_edge(proj, mod_id, rel, alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            # `node.module` is None for a bare relative import (`from . import sib`,
            # `from .. import x`) — a ubiquitous sibling/subpackage idiom. It is internal
            # whenever it is relative; resolve the package base from the level so its
            # module-load edge still fires (panel R14A, cardinal — else a class used only
            # at the imported sibling's module scope is flagged dead).
            internal = (node.level > 0) or bool(
                node.module and node.module.split(".")[0] in proj.packages)
            if not internal:
                continue
            base = _resolve_import_base(rel, node)
            if base and node.module:
                # `from X import y` loads module X (runs its top-level). Resolve X by its
                # EXACT qualname so a same-basename module in another package is never
                # falsely linked (panel R13). `y` may itself be a submodule X.y — try that
                # too. This is what keeps a class used only at X's module scope live.
                _module_load_edge_qual(proj, mod_id, rel, base, node.lineno)
            for alias in node.names:
                if node.module:
                    _import_edge(proj, mod_id, rel, f"{node.module}.{alias.name}",
                                 node.lineno, leaf=alias.name, internal=internal,
                                 pkg_base=base)
                if base:
                    # `from . import sib` imports the submodule `<base>.sib` — load it.
                    _module_load_edge_qual(proj, mod_id, rel, f"{base}.{alias.name}",
                                           node.lineno)

    _walk_scope(proj, rel, tree, parent="", class_qual=None)
    _module_scope_edges(proj, rel, tree, mod_id)
    _global_state(proj, rel, tree)


def _module_scope_edges(proj: _Project, rel: str, tree: ast.Module, mod_id: str) -> None:
    """Calls and by-name references made by module-level *executable* code, attributed to
    the module node. Top-level statements run when the module is loaded, so a class/function
    used only here — a registry value, a dispatch-table entry, or a module-level
    instantiation (`REGISTRY = Builder()`, `_JS = LangSpec(...)`) — is live whenever the
    module loads. Without this the symbol has no incoming edge and live code is flagged dead
    (panel R12, cardinal); the module node propagates this only when it is itself a load
    root (the detector seeds a module that owns any root). Module-level *defs* are NOT
    auto-reached (finding dead ones is dead-code's job) and imports are modelled by
    `_import_edge`; this mirrors the class-body pass that attributes class-level uses to the
    class node."""
    call_funcs: set[int] = set()
    for call in _direct_calls(tree):
        call_funcs.add(id(call.func))
        _call_edge(proj, rel, mod_id, None, {}, call)
    # Module-level attribute reads (`RESULT = _E.compute`) on an unknown receiver need the
    # same name-based REFERENCES fallback as the function-body pass, or a live member read at
    # import is flagged dead — the read-side scope twin of the round-30 fix (panel R31A,
    # cardinal). Module scope has no typed locals, so every receiver is unknown -> fallback.
    for attr in _direct_attr_reads(tree, call_funcs):
        _ref_edges(proj, mod_id, attr.attr, Relation.REFERENCES, rel, attr.lineno,
                   is_method=True)
    for nm in _direct_names(tree, call_funcs):
        _ref_edges(proj, mod_id, nm.id, Relation.REFERENCES, rel, nm.lineno)
    # A module-level *decorated* def runs its decorator(s) at import, which receive/register
    # the def (the plugin/dispatch idiom `@register("x") def x`, or any wrapping decorator).
    # So both the def and each decorator name are used at load — edge them from the module
    # node, or a registry handler and the decorator itself are flagged dead while the
    # equivalent dict-literal registry (`REGISTRY = {"x": x}`) is rescued (panel R15B). The
    # plain `_direct_names` pass misses these: it skips def statements, and a bare-name
    # decorator (`@memo`, no call) has no Name node it collects.
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and stmt.decorator_list:
            did = Node.make_id(rel, stmt.name)  # module-level def: qual is just its name
            if did in proj.ids:
                # INFERRED, not EXTRACTED: the decorator certainly *runs* at load, but
                # whether it makes the def live (register) or just wraps it is heuristic —
                # so a decorated stub stays ORANGE under the provenance ceiling, not RED
                # (matches the route-resolver's INFERRED registration). Liveness still flows.
                proj.edges.append(Edge(
                    src=mod_id, relation=Relation.REFERENCES, dst_symbol=stmt.name,
                    dst_id=did, weight=0.8, provenance=Provenance.INFERRED,
                    location=f"{rel}:{stmt.lineno}:0", source="ast"))
                # Attribute the decorator reference to the DEF (like _decorator_edges does in
                # function scope), not the module node: a `@memo def f` is a use of `memo` by
                # `f`. Edging it from the module instead would add a per-importer edge to the
                # decorator's fan_in and could push a shared decorator over the god_object
                # threshold (panel R16B). The def is reachable via the module->def edge above,
                # so the decorator stays live.
                _decorator_edges(proj, did, stmt, rel)


def _global_state(proj: _Project, rel: str, tree: ast.Module) -> None:
    """Model mutable module-level state for data-loop detection (design §6.F).

    A name is tracked only if some function declares it `global` (intent to
    rebind shared state) — this targets the feedback/accumulator pattern and
    avoids flooding the graph with every constant. Emits WRITES (writer -> var)
    and READS (reader -> var) so a function that both reads and writes a global
    forms a data feedback loop.
    """
    mutable: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            mutable.update(node.names)
    if not mutable:
        return
    for name in mutable:
        proj.nodes.append(Node(id=f"var::{rel}::{name}", kind=NodeKind.VARIABLE,
                               name=name, location=f"{rel}:0:0"))
    for func, fid in _iter_funcs(tree, rel):
        decl: set[str] = set()
        reads: set[str] = set()
        stores: set[str] = set()
        for child in _direct_nodes(func):
            if isinstance(child, ast.Global):
                decl.update(child.names)
            elif isinstance(child, ast.Name) and child.id in mutable:
                if isinstance(child.ctx, ast.Load):
                    reads.add(child.id)
                elif isinstance(child.ctx, ast.Store):
                    stores.add(child.id)
        # A WRITES edge requires the function to actually *assign* a declared global,
        # not merely declare it: `global x; return x` (read-only) must not get a
        # spurious WRITES (which faked a read+write data feedback loop in scan()).
        writes = decl & stores
        for name in writes:
            proj.edges.append(Edge(src=fid, relation=Relation.WRITES, dst_symbol=name,
                                   dst_id=f"var::{rel}::{name}", weight=1.0,
                                   provenance=Provenance.EXTRACTED,
                                   location=f"{rel}:{func.lineno}:0", source="ast"))
        for name in reads:  # a read is a read whether or not the function also writes
            proj.edges.append(Edge(src=fid, relation=Relation.READS, dst_symbol=name,
                                   dst_id=f"var::{rel}::{name}", weight=1.0,
                                   provenance=Provenance.EXTRACTED,
                                   location=f"{rel}:{func.lineno}:0", source="ast"))


def _iter_funcs(tree: ast.Module, rel: str):
    def walk(node, parent):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{parent}.{child.name}" if parent else child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield child, Node.make_id(rel, qual)
                yield from walk(child, qual)
            else:
                # Look through control-flow blocks (a func defined in an `if`/`for`/
                # `try`) without adding a qual level — they aren't a scope in Python.
                yield from walk(child, parent)
    yield from walk(tree, "")


def _def_header_refs(stmt: ast.AST):
    """Expressions in a *nested* def's header that execute in the ENCLOSING scope at
    definition time, not in the def's own body: decorator expressions (incl. their
    argument calls, e.g. `@registry(make_validator())`) and a nested class's base /
    keyword expressions (e.g. `class L(get_base())`). The body-skip that stops a
    nested def's *body* leaking up (Panel R) must NOT also drop these — they run when
    the enclosing function runs, so a symbol used only here is live and would
    otherwise be flagged dead. Parameter defaults/annotations are intentionally
    excluded: `_annotation_names` already attributes them to the nested def itself."""
    yield from getattr(stmt, "decorator_list", [])
    if isinstance(stmt, ast.ClassDef):
        yield from stmt.bases
        for kw in stmt.keywords:
            if kw.value is not None:
                yield kw.value


def _direct_nodes(func: ast.AST):
    """All descendant nodes in a function's own scope (not crossing nested defs)."""
    def rec(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            yield child
            yield from rec(child)
    for stmt in getattr(func, "body", []):
        # A top-level body statement that is *itself* a nested def belongs to that
        # def's own scope, not this one. rec() already guards def *children*, but the
        # driver must guard the top-level stmt too, or the nested def's calls/refs/
        # globals leak up and get mis-attributed to the enclosing scope (double-
        # counting fan_in/pagerank, false god-objects — the metric-inflation class).
        # Its *header* expressions (decorators/bases) still run in THIS scope, though.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for h in _def_header_refs(stmt):
                yield from rec(h)
                yield h
            continue
        yield from rec(stmt)
        yield stmt


def _walk_scope(proj: _Project, rel: str, node: ast.AST, parent: str,
                class_qual: str | None) -> None:
    # `parent` is the relative dotted qual, threaded exactly as _def_node builds
    # node ids, so edge-source ids line up with node ids (incl. for methods).
    #
    # A function-local def (nested class/function) executes when its enclosing
    # function runs — registered (`@app.command`), returned as a closure, or called
    # locally — so it's live iff the enclosing function is reachable. Edge
    # enclosing -> nested, or a handler whose liveness comes from decorator
    # registration (not a direct call) is flagged dead once it's a node. This
    # mirrors Panel P's class-body rule. Only function scopes get this: module and
    # class scopes must NOT auto-reach their members — that is dead-code's whole job.
    enclosing_is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    enclosing_id = Node.make_id(rel, parent) if enclosing_is_func and parent else None
    # `_scope_defs` looks through control-flow blocks (a def in an `if`/`for`/`try`) so
    # a control-flow-nested def gets its edges + containment edge — must match the
    # identical traversal in `_def_node`, which models the nodes these edges target.
    for child in _scope_defs(node):
        if isinstance(child, ast.ClassDef):
            qual = f"{parent}.{child.name}" if parent else child.name
            cid = Node.make_id(rel, qual)
            if enclosing_id:
                _add_ref(proj, enclosing_id, child.name, cid, rel, child.lineno)
            for base in child.bases:
                name = _name_of(base)
                if name:
                    _ref_edges(proj, cid, name, Relation.INHERITS, rel, child.lineno)
                    if name not in proj.class_by_name and name not in _PLAIN_BASES:
                        proj.external_base_classes.add(cid)  # framework base
            # Class-definition keyword args (`metaclass=Meta`, and similar) reference a
            # symbol at the same syntactic level as the bases — a metaclass governs the
            # class's creation, so it's live. Edge it -> REFERENCES or it's flagged dead.
            for kw in child.keywords:
                kw_name = _name_of(kw.value)
                if kw_name:
                    _ref_edges(proj, cid, kw_name, Relation.REFERENCES, rel, child.lineno)
            # References in the class *body* itself (not in any method) — class-level
            # attribute assignments (`handler = Helper`), dispatch tables
            # (`TABLE = {"a": handle_a}`), and class-level annotations. These are live
            # iff the class is reachable, so attribute them to the class node. The
            # Python ast walks only FunctionDef bodies below; without this the class
            # body's symbols are never edged -> live code flagged dead (matches the
            # tree-sitter extractor, which walks the whole class node).
            # The class body runs the SAME three passes as a function body (calls, attribute
            # reads, name refs) — it is the third scope edge-builder and must stay symmetric
            # with `_module_scope_edges` and the FunctionDef branch, or a use that is edged in
            # one scope is flagged dead in another (the class-body member call `KEPT = _e.m()`
            # was edged in module/function scope but not here — oracle cardinal-matrix cell).
            # `call_funcs` excludes each call's callee from the read/name passes so a call is
            # not also double-counted as a REFERENCES.
            cls_call_funcs: set[int] = set()
            for call in _direct_calls(child):
                cls_call_funcs.add(id(call.func))
                _call_edge(proj, rel, cid, qual, {}, call)
            for attr in _direct_attr_reads(child, cls_call_funcs):
                _ref_edges(proj, cid, attr.attr, Relation.REFERENCES, rel, attr.lineno,
                           is_method=True)
            for nm in _direct_names(child, cls_call_funcs):
                _ref_edges(proj, cid, nm.id, Relation.REFERENCES, rel, nm.lineno)
            _walk_scope(proj, rel, child, parent=qual, class_qual=qual)
            _decorator_edges(proj, cid, child, rel)
            # Constructing the class implicitly runs its constructor hooks, so link
            # class -> __init__/__new__/__post_init__. Without this, a class built only
            # via `Foo()` leaves its __init__ (and whatever __init__ constructs, e.g.
            # `Resource()`) unreachable -> false dead-code (live code flagged dead).
            for dunder in ("__init__", "__new__", "__post_init__"):
                mid = Node.make_id(rel, f"{qual}.{dunder}")
                if mid in proj.ids:
                    _add_ref(proj, cid, dunder, mid, rel, child.lineno)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{parent}.{child.name}" if parent else child.name
            cid = Node.make_id(rel, qual)
            if enclosing_id:
                _add_ref(proj, enclosing_id, child.name, cid, rel, child.lineno)
            _decorator_edges(proj, cid, child, rel)
            local_types = _local_types(proj, child)
            call_funcs = set()
            for call in _direct_calls(child):
                call_funcs.add(id(call.func))
                _call_edge(proj, rel, cid, class_qual, local_types, call)
            # Attribute *reads* (e.g. a property `x.resolved`) -> REFERENCES, so a
            # used property/method isn't wrongly flagged dead.
            for attr in _direct_attr_reads(child, call_funcs):
                tid, exact = _resolve_member(proj, rel, class_qual, local_types,
                                             attr.attr, attr.value)
                if tid:
                    _add_ref(proj, cid, attr.attr, tid, rel, attr.lineno)
                    if not exact:
                        # A property/attribute read through a declared (annotation) type is
                        # the read-side twin of the call-site widening: the runtime object
                        # may be a subclass / structural Protocol impl / duck-typed class,
                        # so widen REFERENCES to all same-named members or the live override
                        # (and its private helpers) is flagged dead (panel R21A, cardinal).
                        _ref_edges(proj, cid, attr.attr, Relation.REFERENCES, rel,
                                   attr.lineno)
                else:
                    # Receiver type unknown (a constructor result `Config().threshold`, or an
                    # unannotated parameter `def f(cfg): return cfg.threshold`): emit the
                    # name-based REFERENCES fallback — the read-side twin of `_call_edge`'s
                    # unknown-receiver CALLS fallback. Without it a live property/attribute read
                    # on an unknown receiver (and its private helpers) is flagged dead — and an
                    # unannotated-parameter attribute read is an everyday shape (panel R30A,
                    # cardinal). Over-approximated through `_ref_edges` (only project symbols
                    # resolve, INFERRED), exactly as the call path does.
                    _ref_edges(proj, cid, attr.attr, Relation.REFERENCES, rel,
                               attr.lineno, is_method=True)
            # Bare-name *value* references (not the callee of a call): a function or
            # class passed by name (`register(handler)`, `fn = worker`), or a class
            # accessed as `Color.RED` / `Widget.create()` (the receiver is a bare
            # Name). These are real uses the extractor sees, so edge them -> REFERENCES
            # or the live symbol is wrongly flagged dead. Over-approximated through
            # `_ref_edges` (only project symbols resolve), like the attr-read pass.
            for nm in _direct_names(child, call_funcs):
                _ref_edges(proj, cid, nm.id, Relation.REFERENCES, rel, nm.lineno)
            # Parameter / return *type annotations* live in `child.args` / `child.returns`,
            # not the body, so the passes above never see them. A class used only as a
            # type annotation from a live function is still a real use -> REFERENCES, or
            # it is wrongly flagged dead (the tree-sitter extractor already covers this
            # by walking the whole def node).
            for ann_name in _annotation_names(child):
                _ref_edges(proj, cid, ann_name, Relation.REFERENCES, rel, child.lineno)
            # `with EXPR as ...` exercises the context manager's __enter__/__exit__.
            for cm in _direct_withs(child):
                _with_edges(proj, rel, cid, class_qual, local_types, cm)
            # Nested defs keep the enclosing class context (for closed-over self).
            _walk_scope(proj, rel, child, parent=qual, class_qual=class_qual)


def _decorator_edges(proj: _Project, node_id: str, node: ast.AST, rel: str) -> None:
    """A decorated def references its decorator — so a used decorator stays live."""
    for d in getattr(node, "decorator_list", []):
        name = _name_of(d.func) if isinstance(d, ast.Call) else _name_of(d)
        if name:
            _ref_edges(proj, node_id, name, Relation.REFERENCES, rel,
                       getattr(d, "lineno", getattr(node, "lineno", 0)))


def _call_edge(proj: _Project, rel: str, src_id: str, class_qual: str | None,
               local_types: dict[str, str], call: ast.Call) -> None:
    """Resolve one call site, scope-aware (design §5 — sharper than name-only).

    self.m()/cls.m() -> the enclosing class's method; var.m() where var has a
    known local type -> that class's method; bare m() / unknown -> name-based.
    """
    func = call.func
    line = call.lineno

    if isinstance(func, ast.Attribute):
        tid, exact = _resolve_member(proj, rel, class_qual, local_types, func.attr,
                                     func.value)
        if tid:
            _add_call(proj, src_id, func.attr, tid, rel, line, weight=1.0,
                      prov=Provenance.EXTRACTED)
            if not exact:
                # Declared type is a hint (subclass / structural Protocol / duck typing):
                # widen to every same-named method so a live override/implementation is
                # never flagged dead (panel R20A, cardinal). The precise edge above stays
                # EXTRACTED — `_dedup_edges` keeps the higher weight; the rest go AMBIGUOUS.
                _ref_edges(proj, src_id, func.attr, Relation.CALLS, rel, line,
                           is_method=True)
            return
        # Receiver type unknown (not self/cls, not a locally-typed var): the name-only
        # bind to a lone same-named method is a guess, not an extraction (issue #10) —
        # `recv` may be a stdlib/third-party type. Mark it INFERRED. Weight stays 1.0
        # so reachability/find_stale are unchanged (cardinal-safe); mirrors the
        # tree-sitter extractor's receiver-call demotion.
        _ref_edges(proj, src_id, func.attr, Relation.CALLS, rel, line, is_method=True)
        return

    name = _name_of(func)
    if name:
        _ref_edges(proj, src_id, name, Relation.CALLS, rel, line)


def _resolve_member(proj: _Project, rel: str, class_qual: str | None,
                    local_types: dict[str, str], attr: str,
                    recv: ast.AST) -> tuple[str | None, bool]:
    """Resolve `recv.attr` to a member node id, scope-aware (self / local type).

    Returns (node_id, exact). `exact` is True only for a `self`/`cls` receiver, whose
    runtime type IS the enclosing class (or a subclass, handled by `_propagate_overrides`).
    A binding via a declared local/parameter TYPE is `exact=False`: the annotation is only
    a hint — the runtime object may be a subclass, a structural `Protocol` implementer, or
    an unrelated duck-typed class with the same method (panel R20A), so the caller must
    widen to all same-named methods to stay cardinal-safe."""
    if not isinstance(recv, ast.Name):
        return None, False
    if recv.id in ("self", "cls") and class_qual:
        tid = Node.make_id(rel, f"{class_qual}.{attr}")
        if tid in proj.ids:
            return tid, True
    cls = local_types.get(recv.id)
    if cls:
        for class_id in proj.class_by_name.get(cls, []):
            mid = f"{class_id}.{attr}"
            if mid in proj.ids:
                return mid, False
    return None, False


def _add_call(proj: _Project, src_id: str, symbol: str, dst_id: str, rel: str,
              line: int, weight: float, prov: Provenance) -> None:
    proj.edges.append(Edge(src=src_id, relation=Relation.CALLS, dst_symbol=symbol,
                           dst_id=dst_id, weight=weight, provenance=prov,
                           location=f"{rel}:{line}:0", source="ast"))


def _add_ref(proj: _Project, src_id: str, symbol: str, dst_id: str, rel: str,
             line: int) -> None:
    proj.edges.append(Edge(src=src_id, relation=Relation.REFERENCES, dst_symbol=symbol,
                           dst_id=dst_id, weight=0.95, provenance=Provenance.EXTRACTED,
                           location=f"{rel}:{line}:0", source="ast"))


def _with_edges(proj: _Project, rel: str, src_id: str, class_qual: str | None,
                local_types: dict[str, str], item: ast.withitem) -> None:
    """A `with` context manager uses __enter__/__exit__ — reference them so they
    aren't flagged dead, and the cleanup they call (e.g. close) stays live."""
    expr = item.context_expr
    cls_ids: list[str] = []
    if isinstance(expr, ast.Call):
        ctor = _name_of(expr.func)
        if ctor:
            cls_ids = proj.class_by_name.get(ctor, [])
    elif isinstance(expr, ast.Name) and expr.id in local_types:
        cls_ids = proj.class_by_name.get(local_types[expr.id], [])
    for cid in cls_ids:
        for dunder in ("__enter__", "__exit__"):
            mid = f"{cid}.{dunder}"
            if mid in proj.ids:
                _add_ref(proj, src_id, dunder, mid, rel,
                         getattr(expr, "lineno", 0))


def _direct_attr_reads(func: ast.AST, call_funcs: set[int]) -> list[ast.Attribute]:
    """Attribute accesses in Load context that aren't the callee of a call."""
    out: list[ast.Attribute] = []

    def rec(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load) \
                    and id(child) not in call_funcs:
                out.append(child)
            rec(child)

    for stmt in getattr(func, "body", []):
        # Skip a top-level body statement that is itself a nested def: its contents
        # belong to that def's own scope (rec() guards def children, but not the
        # driver's own stmt), or its calls/refs leak up into this scope. For a class
        # passed here (Panel P class-body walk), this correctly keeps only the class
        # body's own statements and excludes method bodies.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for h in _def_header_refs(stmt):  # decorators/bases run in THIS scope
                rec(h)
            continue
        rec(stmt)
    return out


def _annotation_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Symbol names referenced in a function's *signature*: parameter/return type
    annotations (incl. generic args `list[T]` and string forward refs `"T"`) AND
    parameter default-value expressions (`def f(x=Strategy, cb=handler)`). All live
    in `func.args`/`func.returns`, not the body, so the body pass misses them; a
    class/fn used only as an annotation or a default executes at runtime and must
    not be flagged dead (tree-sitter's `_direct_refs` already walks the whole def)."""
    a = func.args
    exprs: list[ast.expr | None] = [arg.annotation
                                    for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    exprs += [a.vararg.annotation if a.vararg else None,
              a.kwarg.annotation if a.kwarg else None, func.returns]
    exprs += list(a.defaults) + list(a.kw_defaults)  # default *values* reference symbols
    out: list[str] = []
    for ex in exprs:
        if ex is None:
            continue
        for n in ast.walk(ex):
            if isinstance(n, ast.Name):
                out.append(n.id)
            elif isinstance(n, ast.Attribute):
                out.append(n.attr)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                # forward ref like "Config" or "list[Config]" -> the bare identifiers
                out += re.findall(r"[A-Za-z_]\w*", n.value)
    return out


def _direct_names(func: ast.AST, call_funcs: set[int]) -> list[ast.Name]:
    """Load-context `Name` nodes in the function's own scope (not crossing nested
    defs), excluding the callee of a call (already modelled as CALLS). These are
    by-name value references — a symbol passed/assigned by name, or the bare-class
    receiver of `Class.member`."""
    out: list[ast.Name] = []

    def rec(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) \
                    and id(child) not in call_funcs:
                out.append(child)
            rec(child)

    for stmt in getattr(func, "body", []):
        # Skip a top-level body statement that is itself a nested def: its contents
        # belong to that def's own scope (rec() guards def children, but not the
        # driver's own stmt), or its calls/refs leak up into this scope. For a class
        # passed here (Panel P class-body walk), this correctly keeps only the class
        # body's own statements and excludes method bodies.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for h in _def_header_refs(stmt):  # decorators/bases run in THIS scope
                rec(h)
            continue
        rec(stmt)
    return out


def _direct_withs(func: ast.AST) -> list[ast.withitem]:
    out: list[ast.withitem] = []

    def rec(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, (ast.With, ast.AsyncWith)):
                out.extend(child.items)
            rec(child)

    for stmt in getattr(func, "body", []):
        # Skip a top-level body statement that is itself a nested def: its contents
        # belong to that def's own scope (rec() guards def children, but not the
        # driver's own stmt), or its calls/refs leak up into this scope. For a class
        # passed here (Panel P class-body walk), this correctly keeps only the class
        # body's own statements and excludes method bodies.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for h in _def_header_refs(stmt):  # decorators/bases run in THIS scope
                rec(h)
            continue
        rec(stmt)
    return out


def _local_types(proj: _Project, func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """Map local variable names to class names via param annotations, annotated
    assignments, and `x = ClassName(...)` constructions (a light, dependency-free
    approximation of what an LSP/jedi resolves precisely)."""
    types: dict[str, str] = {}
    for arg in (*func.args.args, *func.args.posonlyargs, *func.args.kwonlyargs):
        ann = _annotation_name(arg.annotation)
        if ann and ann in proj.class_by_name:
            types[arg.arg] = ann
    for stmt in ast.walk(func):
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            ann = _annotation_name(stmt.annotation)
            if ann and ann in proj.class_by_name:
                types[stmt.target.id] = ann
        elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            ctor = _name_of(stmt.value.func)
            if ctor and ctor in proj.class_by_name:
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        types[t.id] = ctor
    return types


def _annotation_name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):  # e.g. Optional[Foo], list[Foo]
        return _annotation_name(node.slice)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value  # string annotation
    return None


def _direct_calls(func: ast.AST) -> list[ast.Call]:
    """Calls in this function's own body, not descending into nested defs."""
    out: list[ast.Call] = []

    def rec(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call):
                out.append(child)
            rec(child)

    for stmt in getattr(func, "body", []):
        # Skip a top-level body statement that is itself a nested def: its contents
        # belong to that def's own scope (rec() guards def children, but not the
        # driver's own stmt), or its calls/refs leak up into this scope. For a class
        # passed here (Panel P class-body walk), this correctly keeps only the class
        # body's own statements and excludes method bodies.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for h in _def_header_refs(stmt):  # decorators/bases run in THIS scope
                rec(h)
            continue
        rec(stmt)
    return out


# -- edge builders (precision-biased multi-candidate resolution) ------------
def _ref_edges(proj: _Project, src_id: str, name: str, relation: Relation,
               rel: str, line: int, is_method: bool = False) -> None:
    cands = proj.by_name.get(name, [])
    # A call/by-name reference must not bind to a MODULE node: `_index` aliases module
    # nodes by their short name (for `from pkg import submodule` import resolution), but a
    # `helper()` call or a value reference to `helper` is never a module — binding it to a
    # same-basename module in another package falsely linked that module live, masking its
    # dead code and inflating impact_of (panel R13B). Imports keep module resolution
    # (`_import_edge` / module-load edges), which don't go through here.
    if cands:
        # Drop module candidates AND collapse duplicate ids: a function named like its own
        # module (`def compute()` in `compute.py`) makes the MODULE and FUNCTION nodes share
        # one id, which `_index` lists twice in by_name — without dedup `_ref_edges` sees two
        # candidates and wrongly emits an AMBIGUOUS 0.5 edge for a single real target
        # (panel R15B). dict.fromkeys keeps first-seen order.
        cands = list(dict.fromkeys(c for c in cands if c not in proj.module_ids))
    loc = f"{rel}:{line}:0"
    if not cands:
        return  # external / builtin / unknown -> drop (call holes are unreliable)
    if len(cands) == 1:
        # A receiver-based member resolution (`recv.m()` OR a read `recv.attr`) whose receiver
        # type we couldn't resolve is a name-only guess even with one candidate (issue #10):
        # INFERRED, not EXTRACTED. Gated on `is_method` ALONE, not the relation — a name-based
        # attribute READ (REFERENCES) is exactly as uncertain as a name-based CALL, and gating
        # on CALLS left the read EXTRACTED, so a stub reached only via it shouted RED instead
        # of ORANGE (panel R31B, inflation). Weight unchanged -> never under-counts (cardinal-
        # safe); a bare-name reference (is_method=False) stays EXTRACTED (the name is exact).
        prov = Provenance.INFERRED if is_method else Provenance.EXTRACTED
        proj.edges.append(Edge(src=src_id, relation=relation, dst_symbol=name,
                               dst_id=cands[0], weight=1.0,
                               provenance=prov, location=loc, source="ast",
                               name_based=True))
        return
    # Several candidates: over-approximate so a live symbol is never called dead.
    w = round(1.0 / len(cands), 3)
    for cid in cands:
        proj.edges.append(Edge(src=src_id, relation=relation, dst_symbol=name,
                               dst_id=cid, weight=w, provenance=Provenance.AMBIGUOUS,
                               location=loc, source="ast", name_based=True))


def _resolve_import_base(rel: str, node: ast.ImportFrom) -> str | None:
    """Absolute module qualname of a `from <module> import ...` target, resolving the
    relative level against the importer's package. `from .conf import x` in `pkg/api.py`
    -> `pkg.conf`; `from pkg2 import y` -> `pkg2`. Returns None if it can't be resolved."""
    if node.level == 0:
        return node.module
    pkg = _module_qualname(rel).split(".")
    # The importer's containing package: a package __init__ IS its package; a module file
    # drops its own trailing name. Each level beyond the first drops one more component.
    container = pkg if rel.endswith("__init__.py") else pkg[:-1]
    keep = len(container) - (node.level - 1)
    if keep < 0:
        return None
    base = container[:keep]
    if node.module:
        return ".".join([*base, node.module])
    return ".".join(base) if base else None


def _module_load_edge_qual(proj: _Project, src_id: str, rel: str, qualname: str,
                           line: int) -> None:
    """Link the importer to the EXACT module node named `qualname` (its top-level runs on
    import). Resolved by exact qualname, so a same-basename module elsewhere is not linked
    (panel R13). Module nodes are never dead-code candidates, so this only confers
    liveness — a class used only at the imported module's top level stays live (panel R12)."""
    m_id = proj.module_by_qual.get(qualname)
    if m_id and m_id != src_id:
        proj.edges.append(Edge(src=src_id, relation=Relation.IMPORTS, dst_symbol=qualname,
                               dst_id=m_id, weight=1.0, provenance=Provenance.EXTRACTED,
                               location=f"{rel}:{line}:0", source="ast"))


def _module_load_edge(proj: _Project, src_id: str, rel: str, dotted: str, line: int) -> None:
    """`import a.b.c` loads a.b.c (and its parent packages). Link to each that resolves to
    a known module node, by exact qualname (panel R13)."""
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        _module_load_edge_qual(proj, src_id, rel, ".".join(parts[:i]), line)


def _import_edge(proj: _Project, src_id: str, rel: str, dotted: str, line: int,
                 leaf: str | None = None, internal: bool | None = None,
                 pkg_base: str | None = None) -> None:
    root = dotted.split(".")[0]
    is_internal = internal if internal is not None else (root in proj.packages)
    if not is_internal:
        return  # external dependency, not a hole
    symbol = leaf or dotted.split(".")[-1]
    cands = list(dict.fromkeys(proj.by_name.get(symbol, [])))  # collapse duplicate ids (R15B)
    if pkg_base and len(cands) > 1:
        # Disambiguate by the import's package: `from pkg1 import helper` must not bind to a
        # `helper` (function OR same-basename module, which `_index` aliases globally) in an
        # unrelated pkg2 — keep only candidates whose owning module is the imported package
        # or under it. Avoids a false cross-package link that masks dead code and inflates
        # impact_of (panel R13B). Fall back to all candidates if none match (cardinal-safe).
        # `pkg_base` is the import as written (`flake8.formatting`); a candidate's qualname is
        # path-derived and in a src-layout carries the `src.` prefix the import omits — strip
        # it before comparing so the scope match still fires (else it falls through to the
        # cardinal-safe keep-all fallback and loses precision).
        def _scope_qual(cid: str) -> str:
            mq = _module_qualname(cid.split("::", 1)[0])
            if proj.source_prefix and mq.startswith(proj.source_prefix):
                mq = mq[len(proj.source_prefix):]
            return mq
        scoped = [c for c in cands
                  if (mq := _scope_qual(c)) == pkg_base or mq.startswith(pkg_base + ".")]
        if scoped:
            cands = scoped
    loc = f"{rel}:{line}:0"
    if not cands:
        if symbol in proj.module_consts:
            return  # a module-level constant (not graphed as a node) — not a hole
        # Internal import that doesn't resolve -> a genuine hole (design §6.D).
        proj.edges.append(Edge(src=src_id, relation=Relation.IMPORTS, dst_symbol=symbol,
                               dst_id=None, weight=0.6, provenance=Provenance.INFERRED,
                               location=loc, source="ast"))
        return
    if len(cands) == 1:
        proj.edges.append(Edge(src=src_id, relation=Relation.IMPORTS, dst_symbol=symbol,
                               dst_id=cands[0], weight=1.0, provenance=Provenance.EXTRACTED,
                               location=loc, source="ast"))
    else:
        w = round(1.0 / len(cands), 3)
        for cid in cands:
            proj.edges.append(Edge(src=src_id, relation=Relation.IMPORTS, dst_symbol=symbol,
                                   dst_id=cid, weight=w, provenance=Provenance.AMBIGUOUS,
                                   location=loc, source="ast"))


# -- ast helpers ------------------------------------------------------------
def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_stub(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [s for s in func.body if not _is_docstring(s)]
    if not body:
        return True
    if len(body) == 1:
        only = body[0]
        if isinstance(only, ast.Pass):
            return True
        if isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant) \
                and only.value.value is Ellipsis:
            return True
        if isinstance(only, ast.Raise) and _raises_notimplemented(only):
            return True
    return False


def _is_abstract(func: ast.FunctionDef | ast.AsyncFunctionDef, in_abstract: bool) -> bool:
    for d in func.decorator_list:
        if _name_of(d) in {"abstractmethod", "abstractproperty"}:
            return True
    return in_abstract


def _is_abstract_class(node: ast.ClassDef) -> bool:
    bases = {_name_of(b) for b in node.bases}
    keywords = {kw.arg: _name_of(kw.value) for kw in node.keywords}
    return bool(bases & {"Protocol", "ABC", "ABCMeta"}) or keywords.get("metaclass") == "ABCMeta"


def _raises_notimplemented(node: ast.Raise) -> bool:
    exc = node.exc
    name = None
    if isinstance(exc, ast.Call):
        name = _name_of(exc.func)
    elif isinstance(exc, ast.Name):
        name = exc.id
    return name in {"NotImplementedError", "NotImplemented"}


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
        and isinstance(stmt.value.value, str)


def _docstring(node: ast.AST) -> str | None:
    try:
        doc = ast.get_docstring(node)  # type: ignore[arg-type]
    except TypeError:
        return None
    if not doc:
        return None
    return " ".join(doc.split())[:200]  # first ~200 chars, whitespace-collapsed


def _arity(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    a = func.args
    return len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs)


def _str_elts(value: ast.AST | None) -> set[str]:
    """String literals reachable in an `__all__` RHS, looking *through* concatenation so
    `["a"] + ["b"]` and `("a",) + OTHER` both yield their literal names (non-literal
    operands contribute nothing). A List/Tuple yields its string constants directly."""
    if isinstance(value, (ast.List, ast.Tuple)):
        return {e.value for e in value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        return _str_elts(value.left) | _str_elts(value.right)
    return set()


def _dunder_all(tree: ast.Module) -> set[str] | None:
    """Names a module declares public via `__all__`. Recognizes every idiomatic build form,
    not just a single list literal: `__all__ = [...]`, `__all__ = [...] + [...]`,
    `__all__ += [...]` (AugAssign), and `__all__.extend([...])` / `.append("x")` calls.
    Missing any of these dropped genuinely-exported symbols' `exported` role, so they were
    flagged dead — live public API as dead, the cardinal sin (panel R28A). Returns None only
    when no `__all__` is present at all (so the caller falls back to other export signals)."""
    found = False
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            found = True
            names |= _str_elts(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "__all__" and isinstance(node.op, ast.Add):
            found = True
            names |= _str_elts(node.value)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            func = call.func
            # `__all__.extend([...])` / `__all__.append("x")`
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                    and func.value.id == "__all__" and func.attr in ("extend", "append"):
                found = True
                for arg in call.args:
                    if func.attr == "append" and isinstance(arg, ast.Constant) \
                            and isinstance(arg.value, str):
                        names.add(arg.value)
                    elif func.attr == "extend":
                        names |= _str_elts(arg)
    return names if found else None


def _main_block(tree: ast.Module) -> ast.If | None:
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) \
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
            return node
    return None


def _has_main_block(tree: ast.Module) -> bool:
    return _main_block(tree) is not None


def _main_block_calls(tree: ast.Module) -> set[str]:
    block = _main_block(tree)
    if block is None:
        return set()
    names: set[str] = set()
    for stmt in block.body:
        for call in (n for n in ast.walk(stmt) if isinstance(n, ast.Call)):
            nm = _name_of(call.func)
            if nm:
                names.add(nm)
    return names


def _module_qualname(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else rel


def _detect_source_prefix(files: list[Path], root: Path) -> str:
    """Return the qualname prefix of a PyPA *src-layout* source root — `"src."` when the
    project keeps its package(s) under a top-level `src/` that is NOT itself a package — else
    `""`. In a src-layout the module `src/pkg/m.py` is installed and imported as `pkg.m`
    (absolute imports say `from pkg import ...`), but its path-derived qualname is
    `src.pkg.m`; without reconciling the two, every absolute first-party import is treated as
    external and dropped, so module-level-only-live code is flagged dead (src-layout cardinal
    gap, surfaced by the flake8 corpus). Scoped to the conventional `src` name to stay
    predictable; `src` itself being a package (a `src/__init__.py`) disables it."""
    rels = [p.relative_to(root).as_posix() for p in files]
    if "src/__init__.py" in rels:
        return ""  # `src` is a real package, not a source root
    # a package directly under src: `src/<pkg>/__init__.py`
    has_pkg_under_src = any(
        r.startswith("src/") and r.endswith("/__init__.py") and r.count("/") == 2
        for r in rels)
    return "src." if has_pkg_under_src else ""


def _project_packages(files: list[Path], root: Path, source_prefix: str = "") -> set[str]:
    pkgs: set[str] = set()
    for path in files:
        parts = path.relative_to(root).parts
        top = parts[0]
        pkgs.add(top[:-3] if top.endswith(".py") else top)
        # src-layout: the importable top-level package is the child of `src/`, not `src`
        # itself (`from flake8 import ...`, not `from src.flake8 import ...`). Add it so the
        # absolute import resolves as internal.
        if source_prefix == "src." and top == "src" and len(parts) > 1:
            child = parts[1]
            pkgs.add(child[:-3] if child.endswith(".py") else child)
    return pkgs


# Directories that are universally dependencies, build output, or VCS metadata — never
# first-party source. Indexing them floods find_stale with thousands of "dead" vendored
# symbols (Win32 headers shipped by tinycc, composer/Go `vendor/`, npm deps) and wastes time
# (WordPress-scale). Shared by the Python and tree-sitter extractors so both stay consistent.
# Conservative: only names that are reserved-by-convention for non-first-party content.
SKIP_DIRS = frozenset({
    ".venv", "venv", "build", "dist", "__pycache__", ".git", ".tox", ".svn", ".hg",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules", "bower_components",
    "vendor", "third_party", "third-party", "target", ".gradle",
})


def _wanted(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return not any(p in SKIP_DIRS for p in parts)


def _ignored(path: Path, root: Path, ignore: list[str] | None) -> bool:
    if not ignore:
        return False
    rel = path.relative_to(root)
    # Skip empty patterns: PurePath.match("") raises ValueError("empty pattern"), so a
    # hand-edited stitchgraph.toml with `ignore = [""]` (or a direct extract_project call)
    # would crash reindex with a raw traceback instead of returning a Result (panel R33B).
    return any(rel.match(pattern) for pattern in ignore if pattern)
