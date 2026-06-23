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
from dataclasses import dataclass, field
from pathlib import Path

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation

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
    files = sorted(p for p in proj.root.rglob("*.py")
                   if _wanted(p, proj.root) and not _ignored(p, proj.root, ignore))
    proj.packages = _project_packages(files, proj.root)

    parsed: dict[str, ast.Module] = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(proj.root).as_posix()
        parsed[rel] = tree
        _collect_defs(proj, rel, path, tree)

    _index(proj)
    _apply_entrypoint_roles(proj)
    for rel, tree in parsed.items():
        _collect_edges(proj, rel, tree)
    _apply_callback_roles(proj)
    return proj.nodes, proj.edges


def _apply_callback_roles(proj: _Project) -> None:
    """Methods of a class with a framework base are likely framework-invoked
    overrides (e.g. HTMLParser.handle_starttag) — mark them 'callback' so they're
    roots, not dead-code false positives (design §7 caveat)."""
    if not proj.external_base_classes:
        return
    for node in proj.nodes:
        if node.kind is NodeKind.METHOD and "." in node.id:
            class_id = node.id.rsplit(".", 1)[0]
            if class_id in proj.external_base_classes:
                node.roles = node.roles | {"callback"}


# -- pass 1: definitions ----------------------------------------------------
def _collect_defs(proj: _Project, rel: str, path: Path, tree: ast.Module) -> None:
    is_init = path.name == "__init__.py"
    is_test_file = path.name.startswith("test_") or path.name.endswith("_test.py")
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
        for node in ast.iter_child_nodes(tree):
            nm = getattr(node, "name", None)
            if nm and not nm.startswith("_"):
                proj.exported_names.add(nm)

    for node in ast.iter_child_nodes(tree):
        _def_node(proj, rel, node, parent="", is_test_file=is_test_file)


def _def_node(proj: _Project, rel: str, node: ast.AST, parent: str,
              is_test_file: bool, in_abstract: bool = False) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        qual = f"{parent}.{node.name}" if parent else node.name
        kind = NodeKind.METHOD if parent else NodeKind.FUNCTION
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
    elif isinstance(node, ast.ClassDef):
        qual = f"{parent}.{node.name}" if parent else node.name
        proj.nodes.append(Node(
            id=Node.make_id(rel, qual), kind=NodeKind.CLASS, name=node.name,
            location=f"{rel}:{node.lineno}:{node.col_offset}",
            end_line=getattr(node, "end_lineno", None), summary=_docstring(node),
        ))
        abstract = _is_abstract_class(node)
        for child in ast.iter_child_nodes(node):
            _def_node(proj, rel, child, parent=qual, is_test_file=is_test_file,
                      in_abstract=abstract)


def _index(proj: _Project) -> None:
    for n in proj.nodes:
        proj.by_name.setdefault(n.name, []).append(n.id)
        proj.ids.add(n.id)
        if n.kind == NodeKind.CLASS:
            proj.class_by_name.setdefault(n.name, []).append(n.id)
        # Alias modules by their short name so `from pkg import submodule` resolves.
        if n.kind == NodeKind.MODULE and "." in n.name:
            proj.by_name.setdefault(n.name.rsplit(".", 1)[-1], []).append(n.id)


def _apply_entrypoint_roles(proj: _Project) -> None:
    """Mark roots once the whole symbol table is known: exported names (public
    API, incl. re-exports) and functions invoked from `__main__` blocks."""
    for node in proj.nodes:
        extra: set[str] = set()
        if node.name in proj.exported_names and node.kind in (
                NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
            extra.add("exported")
        if node.name in proj.main_calls and node.kind == NodeKind.FUNCTION:
            extra.add("main")
        if extra:
            node.roles = node.roles | extra


# -- pass 2: edges ----------------------------------------------------------
def _collect_edges(proj: _Project, rel: str, tree: ast.Module) -> None:
    module_qual = _module_qualname(rel)
    mod_id = Node.make_id(rel, module_qual)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _import_edge(proj, mod_id, rel, alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module:
            internal = (node.level > 0) or node.module.split(".")[0] in proj.packages
            for alias in node.names:
                _import_edge(proj, mod_id, rel, f"{node.module}.{alias.name}",
                             node.lineno, leaf=alias.name, internal=internal)

    _walk_scope(proj, rel, tree, parent="", class_qual=None)
    _global_state(proj, rel, tree)


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
        for child in _direct_nodes(func):
            if isinstance(child, ast.Global):
                decl.update(child.names)
            elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) \
                    and child.id in mutable:
                reads.add(child.id)
        for name in decl & mutable:
            proj.edges.append(Edge(src=fid, relation=Relation.WRITES, dst_symbol=name,
                                   dst_id=f"var::{rel}::{name}", weight=1.0,
                                   provenance=Provenance.EXTRACTED,
                                   location=f"{rel}:{func.lineno}:0", source="ast"))
        for name in reads - decl:  # pure reads (writers already covered above)
            proj.edges.append(Edge(src=fid, relation=Relation.READS, dst_symbol=name,
                                   dst_id=f"var::{rel}::{name}", weight=1.0,
                                   provenance=Provenance.EXTRACTED,
                                   location=f"{rel}:{func.lineno}:0", source="ast"))
        for name in reads & decl:  # read-and-write -> emit both (feedback)
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
    yield from walk(tree, "")


def _direct_nodes(func: ast.AST):
    """All descendant nodes in a function's own scope (not crossing nested defs)."""
    def rec(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            yield child
            yield from rec(child)
    for stmt in getattr(func, "body", []):
        yield from rec(stmt)
        yield stmt


def _walk_scope(proj: _Project, rel: str, node: ast.AST, parent: str,
                class_qual: str | None) -> None:
    # `parent` is the relative dotted qual, threaded exactly as _def_node builds
    # node ids, so edge-source ids line up with node ids (incl. for methods).
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            qual = f"{parent}.{child.name}" if parent else child.name
            cid = Node.make_id(rel, qual)
            for base in child.bases:
                name = _name_of(base)
                if name:
                    _ref_edges(proj, cid, name, Relation.INHERITS, rel, child.lineno)
                    if name not in proj.class_by_name and name not in _PLAIN_BASES:
                        proj.external_base_classes.add(cid)  # framework base
            _walk_scope(proj, rel, child, parent=qual, class_qual=qual)
            _decorator_edges(proj, cid, child, rel)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{parent}.{child.name}" if parent else child.name
            cid = Node.make_id(rel, qual)
            _decorator_edges(proj, cid, child, rel)
            local_types = _local_types(proj, child)
            call_funcs = set()
            for call in _direct_calls(child):
                call_funcs.add(id(call.func))
                _call_edge(proj, rel, cid, class_qual, local_types, call)
            # Attribute *reads* (e.g. a property `x.resolved`) -> REFERENCES, so a
            # used property/method isn't wrongly flagged dead.
            for attr in _direct_attr_reads(child, call_funcs):
                tid = _resolve_member(proj, rel, class_qual, local_types,
                                      attr.attr, attr.value)
                if tid:
                    _add_ref(proj, cid, attr.attr, tid, rel, attr.lineno)
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
        tid = _resolve_member(proj, rel, class_qual, local_types, func.attr, func.value)
        if tid:
            return _add_call(proj, src_id, func.attr, tid, rel, line, weight=1.0,
                             prov=Provenance.EXTRACTED)
        _ref_edges(proj, src_id, func.attr, Relation.CALLS, rel, line)
        return

    name = _name_of(func)
    if name:
        _ref_edges(proj, src_id, name, Relation.CALLS, rel, line)


def _resolve_member(proj: _Project, rel: str, class_qual: str | None,
                    local_types: dict[str, str], attr: str, recv: ast.AST) -> str | None:
    """Resolve `recv.attr` to a member node id, scope-aware (self / local type)."""
    if not isinstance(recv, ast.Name):
        return None
    if recv.id in ("self", "cls") and class_qual:
        tid = Node.make_id(rel, f"{class_qual}.{attr}")
        if tid in proj.ids:
            return tid
    cls = local_types.get(recv.id)
    if cls:
        for class_id in proj.class_by_name.get(cls, []):
            mid = f"{class_id}.{attr}"
            if mid in proj.ids:
                return mid
    return None


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
        rec(stmt)
    return out


# -- edge builders (precision-biased multi-candidate resolution) ------------
def _ref_edges(proj: _Project, src_id: str, name: str, relation: Relation,
               rel: str, line: int) -> None:
    cands = proj.by_name.get(name, [])
    loc = f"{rel}:{line}:0"
    if not cands:
        return  # external / builtin / unknown -> drop (call holes are unreliable)
    if len(cands) == 1:
        proj.edges.append(Edge(src=src_id, relation=relation, dst_symbol=name,
                               dst_id=cands[0], weight=1.0,
                               provenance=Provenance.EXTRACTED, location=loc, source="ast"))
        return
    # Several candidates: over-approximate so a live symbol is never called dead.
    w = round(1.0 / len(cands), 3)
    for cid in cands:
        proj.edges.append(Edge(src=src_id, relation=relation, dst_symbol=name,
                               dst_id=cid, weight=w, provenance=Provenance.AMBIGUOUS,
                               location=loc, source="ast"))


def _import_edge(proj: _Project, src_id: str, rel: str, dotted: str, line: int,
                 leaf: str | None = None, internal: bool | None = None) -> None:
    root = dotted.split(".")[0]
    is_internal = internal if internal is not None else (root in proj.packages)
    if not is_internal:
        return  # external dependency, not a hole
    symbol = leaf or dotted.split(".")[-1]
    cands = proj.by_name.get(symbol, [])
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


def _dunder_all(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__" \
                        and isinstance(node.value, (ast.List, ast.Tuple)):
                    return {e.value for e in node.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return None


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


def _project_packages(files: list[Path], root: Path) -> set[str]:
    pkgs: set[str] = set()
    for path in files:
        top = path.relative_to(root).parts[0]
        pkgs.add(top[:-3] if top.endswith(".py") else top)
    return pkgs


def _wanted(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    skip = {".venv", "venv", "build", "dist", "__pycache__", ".git", ".tox"}
    return not any(p in skip for p in parts)


def _ignored(path: Path, root: Path, ignore: list[str] | None) -> bool:
    if not ignore:
        return False
    rel = path.relative_to(root)
    return any(rel.match(pattern) for pattern in ignore)
