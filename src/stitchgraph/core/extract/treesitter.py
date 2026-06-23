"""Polyglot extractor via tree-sitter (design §0).

A config-driven extractor that produces the *same* node/edge ontology as the
Python extractor — so every supported language lives in one graph and the
cross-language resolvers can stitch them together. Adding a language is a small
`LangSpec`, not new code.

Extracts definitions (functions / methods / classes / structs / traits) and a
call graph (CALLS edges), with precision-biased name resolution (single match ->
confident, several -> AMBIGUOUS to all, unknown -> dropped as external). Entry-
point roles: an exported JS/TS symbol or a `pub` Rust item -> `exported`; any
`main` -> `main`.

Optional dependency (`pip install 'stitchgraph[treesitter]'`). Absent or any
parse error -> those files are skipped; Python extraction is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..model import Edge, Node, NodeKind, Relation
from ..envelope import Provenance

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
    HAS_TREE_SITTER = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_TREE_SITTER = False

F, M, C = NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS


@dataclass(frozen=True)
class LangSpec:
    defs: dict[str, NodeKind]            # node type -> node kind (creates a node)
    call_types: dict[str, str | None]   # call node type -> field for the callee
    containers: frozenset[str] = frozenset()        # types whose children nest (qual)
    container_only: frozenset[str] = frozenset()    # nest qual but create no node
    arrow_decls: bool = False           # JS `const f = () => …`


_JS = LangSpec(
    defs={"function_declaration": F, "generator_function_declaration": F,
          "class_declaration": C, "method_definition": M},
    call_types={"call_expression": "function"},
    containers=frozenset({"class_declaration", "class_body"}),
    arrow_decls=True,
)
SPECS: dict[str, LangSpec] = {
    "javascript": _JS, "typescript": _JS, "tsx": _JS,
    "rust": LangSpec(
        defs={"function_item": F, "struct_item": C, "enum_item": C, "trait_item": C},
        call_types={"call_expression": "function", "macro_invocation": "macro"},
        containers=frozenset({"trait_item", "declaration_list"}),
        container_only=frozenset({"impl_item"}),
    ),
    "c": LangSpec(
        defs={"function_definition": F, "struct_specifier": C},
        call_types={"call_expression": "function"},
        containers=frozenset({"struct_specifier", "field_declaration_list"}),
    ),
    "cpp": LangSpec(
        defs={"function_definition": F, "class_specifier": C, "struct_specifier": C},
        call_types={"call_expression": "function"},
        containers=frozenset({"class_specifier", "struct_specifier",
                              "field_declaration_list"}),
    ),
    "csharp": LangSpec(
        defs={"method_declaration": M, "constructor_declaration": M,
              "class_declaration": C, "struct_declaration": C,
              "interface_declaration": C, "local_function_statement": F},
        call_types={"invocation_expression": "function"},
        containers=frozenset({"class_declaration", "struct_declaration",
                              "interface_declaration", "declaration_list"}),
    ),
    "bash": LangSpec(
        defs={"function_definition": F},
        call_types={"command": None},
    ),
}
EXT_LANG = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".rs": "rust", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".sh": "bash", ".bash": "bash",
}
_SKIP = {".venv", "venv", "build", "dist", "__pycache__", ".git", "node_modules", "target"}


def supported_languages() -> list[str]:
    return sorted(set(EXT_LANG.values()))


def extract(root: str | Path, ignore: list[str] | None = None) -> tuple[list[Node], list[Edge]]:
    if not HAS_TREE_SITTER:
        return [], []
    root = Path(root)
    parsers: dict[str, Parser] = {}
    nodes: list[Node] = []
    defs: list[tuple[str, str, object, str]] = []  # (rel, def_id, body_node, lang)
    src_by: dict[str, bytes] = {}

    files = [p for p in sorted(root.rglob("*"))
             if p.suffix in EXT_LANG and _wanted(p, root, ignore)]
    for path in files:
        lang = EXT_LANG[path.suffix]
        if lang not in SPECS:
            continue
        if lang not in parsers:
            try:
                parsers[lang] = Parser(get_language(lang))
            except Exception:  # noqa: BLE001 — grammar unavailable
                continue
        try:
            src = path.read_bytes()
            tree = parsers[lang].parse(src)
        except (OSError, Exception):  # noqa: BLE001
            continue
        rel = path.relative_to(root).as_posix()
        src_by[rel] = src
        _collect(tree.root_node, src, rel, SPECS[lang], lang,
                 parent="", nodes=nodes, defs=defs, exported=False)

    # Resolve names *within a language* — a JS call must not bind to a Rust fn.
    lang_of: dict[str, str] = {rel: lang for rel, _id, _b, lang in defs}
    by_lang: dict[str, dict[str, list[str]]] = {}
    for n in nodes:
        lang = lang_of.get(n.id.split("::", 1)[0])
        if lang:
            by_lang.setdefault(lang, {}).setdefault(n.name, []).append(n.id)

    edges: list[Edge] = []
    for rel, def_id, body, lang in defs:
        by_name = by_lang.get(lang, {})
        for name, line in _direct_calls(body, src_by[rel], SPECS[lang]):
            _ref(edges, def_id, name, by_name, rel, line)
    return nodes, edges


# -- pass 1: definitions ----------------------------------------------------
def _collect(node, src, rel, spec, lang, parent, nodes, defs, exported):
    for child in node.children:
        t = child.type
        if t == "export_statement":
            _collect(child, src, rel, spec, lang, parent, nodes, defs, exported=True)
        elif t in spec.container_only:
            qual = _join(parent, _name_of(child, src))
            _collect(child, src, rel, spec, lang, qual, nodes, defs, exported=False)
        elif t in spec.defs:
            name = _name_of(child, src)
            if not name:
                _collect(child, src, rel, spec, lang, parent, nodes, defs, False)
                continue
            qual = _join(parent, name)
            roles = _roles(child, src, name, lang, exported)
            nodes.append(Node(id=f"{rel}::{qual}", kind=spec.defs[t], name=name,
                              location=_loc(rel, child), end_line=child.end_point[0] + 1,
                              roles=roles))
            defs.append((rel, f"{rel}::{qual}", child, lang))
            inner = qual if (t in spec.containers or spec.defs[t] is C) else parent
            _collect(child, src, rel, spec, lang, inner, nodes, defs, False)
        elif spec.arrow_decls and t == "variable_declarator":
            val = child.child_by_field_name("value")
            name = _field_text(child, "name", src)
            if name and val and val.type in ("arrow_function", "function", "function_expression"):
                qual = _join(parent, name)
                nodes.append(Node(id=f"{rel}::{qual}", kind=F, name=name,
                                  location=_loc(rel, val), end_line=val.end_point[0] + 1,
                                  roles=frozenset({"exported"}) if exported else frozenset()))
                defs.append((rel, f"{rel}::{qual}", val, lang))
        else:
            _collect(child, src, rel, spec, lang, parent, nodes, defs, exported)


# -- pass 2: calls ----------------------------------------------------------
def _direct_calls(body, src, spec):
    out: list[tuple[str, int]] = []

    def rec(n, top):
        for c in n.children:
            if not top and (c.type in spec.defs or c.type in spec.container_only):
                continue
            if c.type in spec.call_types:
                name = _callee(c, src, spec.call_types[c.type])
                if name:
                    out.append((name, c.start_point[0] + 1))
            rec(c, False)

    rec(body, True)
    return out


def _callee(call, src, field):
    if field is None:  # bash: command -> command_name
        cn = next((c for c in call.children if c.type == "command_name"), None)
        if cn is not None:
            return _text(cn.children[0] if cn.children else cn, src)
        return None
    fn = call.child_by_field_name(field)
    return _trailing_id(fn, src) if fn is not None else None


def _trailing_id(node, src):
    if node is None:
        return None
    if node.type in ("identifier", "type_identifier", "field_identifier",
                     "property_identifier", "word"):
        return _text(node, src)
    prop = node.child_by_field_name("property") or node.child_by_field_name("name")
    if prop is not None:
        return _trailing_id(prop, src)
    for c in reversed(node.children):  # rightmost identifier-ish leaf
        got = _trailing_id(c, src)
        if got:
            return got
    return None


def _ref(edges, src_id, name, by_name, rel, line):
    cands = by_name.get(name, [])
    loc = f"{rel}:{line}:0"
    if not cands:
        return
    if len(cands) == 1:
        edges.append(Edge(src=src_id, relation=Relation.CALLS, dst_symbol=name,
                          dst_id=cands[0], weight=1.0, provenance=Provenance.EXTRACTED,
                          location=loc, source="tree-sitter"))
    else:
        w = round(1.0 / len(cands), 3)
        for cid in cands:
            edges.append(Edge(src=src_id, relation=Relation.CALLS, dst_symbol=name,
                              dst_id=cid, weight=w, provenance=Provenance.AMBIGUOUS,
                              location=loc, source="tree-sitter"))


# -- helpers ---------------------------------------------------------------
def _name_of(node, src):
    nm = node.child_by_field_name("name") or node.child_by_field_name("type")
    if nm is not None:
        return _trailing_id(nm, src)
    decl = node.child_by_field_name("declarator")  # C / C++
    while decl is not None:
        if decl.type in ("identifier", "field_identifier"):
            return _text(decl, src)
        nxt = decl.child_by_field_name("declarator")
        if nxt is None:
            ident = next((c for c in decl.children
                          if c.type in ("identifier", "field_identifier")), None)
            return _text(ident, src) if ident else None
        decl = nxt
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "property_identifier"):
            return _text(c, src)
    return None


def _roles(node, src, name, lang, exported):
    roles = set()
    if exported:
        roles.add("exported")
    if name == "main":
        roles.add("main")
    if lang == "rust" and any(c.type == "visibility_modifier" for c in node.children):
        roles.add("exported")
    return frozenset(roles)


def _join(parent, name):
    return f"{parent}.{name}" if parent and name else (name or parent)


def _field_text(node, field, src):
    child = node.child_by_field_name(field)
    return _text(child, src) if child is not None else None


def _text(node, src):
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _loc(rel, node):
    return f"{rel}:{node.start_point[0] + 1}:{node.start_point[1]}"


def _wanted(path, root, ignore):
    rel = path.relative_to(root)
    if any(p in _SKIP for p in rel.parts):
        return False
    return not (ignore and any(rel.match(pat) for pat in ignore))
