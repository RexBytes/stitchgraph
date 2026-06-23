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
    packages: set[str] = field(default_factory=set)
    exported_names: set[str] = field(default_factory=set)
    main_calls: set[str] = field(default_factory=set)


def extract_project(root: str | Path) -> tuple[list[Node], list[Edge]]:
    """Two passes: (1) collect definitions + symbol table, (2) resolve references."""
    proj = _Project(root=Path(root))
    files = sorted(p for p in proj.root.rglob("*.py") if _wanted(p, proj.root))
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
    return proj.nodes, proj.edges


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
            is_stub=is_stub, arity=_arity(node), roles=frozenset(roles),
        ))
    elif isinstance(node, ast.ClassDef):
        qual = f"{parent}.{node.name}" if parent else node.name
        proj.nodes.append(Node(
            id=Node.make_id(rel, qual), kind=NodeKind.CLASS, name=node.name,
            location=f"{rel}:{node.lineno}:{node.col_offset}",
        ))
        abstract = _is_abstract_class(node)
        for child in ast.iter_child_nodes(node):
            _def_node(proj, rel, child, parent=qual, is_test_file=is_test_file,
                      in_abstract=abstract)


def _index(proj: _Project) -> None:
    for n in proj.nodes:
        proj.by_name.setdefault(n.name, []).append(n.id)
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

    _walk_scope(proj, rel, tree, scope=module_qual)


def _walk_scope(proj: _Project, rel: str, node: ast.AST, scope: str) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qual = _child_qual(scope, rel, child.name)
            cid = Node.make_id(rel, qual)
            if isinstance(child, ast.ClassDef):
                for base in child.bases:
                    name = _name_of(base)
                    if name:
                        _ref_edges(proj, cid, name, Relation.INHERITS, rel, child.lineno)
            else:
                for call in _calls_in(child):
                    name = _name_of(call.func)
                    if name:
                        _ref_edges(proj, cid, name, Relation.CALLS, rel, call.lineno)
            _walk_scope(proj, rel, child, scope=qual)


def _child_qual(scope: str, rel: str, name: str) -> str:
    module_qual = _module_qualname(rel)
    inner = scope[len(module_qual):].lstrip(".") if scope.startswith(module_qual) else ""
    return f"{inner}.{name}" if inner else name


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
def _calls_in(func: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(func) if isinstance(n, ast.Call)]


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
