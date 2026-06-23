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

from dataclasses import dataclass
from pathlib import Path

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation

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
    heritage: frozenset[str] = frozenset()          # child types holding base classes
    imports: frozenset[str] = frozenset()           # import statement node types


_JS = LangSpec(
    defs={"function_declaration": F, "generator_function_declaration": F,
          "class_declaration": C, "method_definition": M},
    call_types={"call_expression": "function"},
    containers=frozenset({"class_declaration", "class_body"}),
    arrow_decls=True,
    heritage=frozenset({"class_heritage"}),
    imports=frozenset({"import_statement"}),
)
SPECS: dict[str, LangSpec] = {
    "javascript": _JS, "typescript": _JS, "tsx": _JS,
    "rust": LangSpec(
        defs={"function_item": F, "struct_item": C, "enum_item": C, "trait_item": C},
        call_types={"call_expression": "function", "macro_invocation": "macro"},
        containers=frozenset({"trait_item", "declaration_list"}),
        container_only=frozenset({"impl_item"}),
        imports=frozenset({"use_declaration"}),
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
        heritage=frozenset({"base_class_clause"}),
    ),
    "csharp": LangSpec(
        defs={"method_declaration": M, "constructor_declaration": M,
              "class_declaration": C, "struct_declaration": C,
              "interface_declaration": C, "local_function_statement": F},
        call_types={"invocation_expression": "function"},
        containers=frozenset({"class_declaration", "struct_declaration",
                              "interface_declaration", "declaration_list"}),
        heritage=frozenset({"base_list"}),
        imports=frozenset({"using_directive"}),
    ),
    "bash": LangSpec(
        defs={"function_definition": F},
        call_types={"command": None},
    ),
    "go": LangSpec(
        defs={"function_declaration": F, "method_declaration": M, "type_spec": C},
        call_types={"call_expression": "function"},
        containers=frozenset({"type_spec"}),
        imports=frozenset({"import_declaration"}),
    ),
    "java": LangSpec(
        defs={"method_declaration": M, "constructor_declaration": M,
              "class_declaration": C, "interface_declaration": C,
              "enum_declaration": C, "record_declaration": C},
        call_types={"method_invocation": "name", "object_creation_expression": "type"},
        containers=frozenset({"class_declaration", "class_body", "interface_declaration",
                              "interface_body", "enum_declaration", "enum_body"}),
        heritage=frozenset({"superclass", "super_interfaces"}),
        imports=frozenset({"import_declaration"}),
    ),
    "ruby": LangSpec(
        defs={"method": M, "singleton_method": M, "class": C, "module": C},
        call_types={"call": "method"},
        containers=frozenset({"class", "module"}),
        heritage=frozenset({"superclass"}),
    ),
    "php": LangSpec(
        defs={"function_definition": F, "method_declaration": M, "class_declaration": C,
              "interface_declaration": C, "trait_declaration": C},
        call_types={"function_call_expression": "function",
                    "member_call_expression": "name", "scoped_call_expression": "name"},
        containers=frozenset({"class_declaration", "declaration_list",
                              "interface_declaration", "trait_declaration"}),
        heritage=frozenset({"base_clause", "class_interface_clause"}),
        imports=frozenset({"namespace_use_declaration"}),
    ),
}
EXT_LANG = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".rs": "rust", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".sh": "bash", ".bash": "bash", ".go": "go", ".java": "java", ".rb": "ruby",
    ".php": "php",
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
    inherits: list[tuple[str, str, str]] = []      # (class_id, base_name, lang)
    imports: list[tuple[str, str, str]] = []       # (mod_id, name, lang)
    src_by: dict[str, bytes] = {}
    file_lang: dict[str, str] = {}

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
        file_lang[rel] = lang
        spec = SPECS[lang]
        mod_id = f"{rel}::{path.stem}"
        nodes.append(Node(id=mod_id, kind=NodeKind.MODULE, name=path.stem,
                          location=f"{rel}:1:0"))
        is_test = _is_test_file(rel)
        _collect(tree.root_node, src, rel, spec, lang, parent="", nodes=nodes,
                 defs=defs, inherits=inherits, exported=False, is_test=is_test)
        for name in _import_names(tree.root_node, src, spec):
            imports.append((mod_id, name, lang))

    _seed_exported_class_methods(nodes)

    # Resolve names *within a language* — a JS call must not bind to a Rust fn.
    by_lang: dict[str, dict[str, list[str]]] = {}
    for n in nodes:
        flang = file_lang.get(n.id.split("::", 1)[0])
        if flang:
            by_lang.setdefault(flang, {}).setdefault(n.name, []).append(n.id)

    edges: list[Edge] = []
    for rel, def_id, body, lang in defs:
        by_name = by_lang.get(lang, {})
        for name, line in _direct_calls(body, src_by[rel], SPECS[lang]):
            _ref(edges, def_id, name, by_name, rel, line)
    for class_id, base, lang in inherits:
        _ref(edges, class_id, base, by_lang.get(lang, {}),
             class_id.split("::", 1)[0], 0, relation=Relation.INHERITS)
    for mod_id, name, lang in imports:
        _ref(edges, mod_id, name, by_lang.get(lang, {}),
             mod_id.split("::", 1)[0], 0, relation=Relation.IMPORTS)
    return nodes, edges


def _seed_exported_class_methods(nodes):
    """Public methods of an exported class are themselves public API — external
    callers reach them, so they're never dead for lack of an internal caller
    (precision over recall). Mirrors the Python extractor's `_apply_entrypoint_roles`,
    closing the symmetry gap for JS/TS and any language where method visibility is
    inherited from the class rather than tokenized per method (Go/Java/C#/PHP/Rust
    already carry per-method visibility, so they're unaffected)."""
    exported_class_ids = {n.id for n in nodes if n.kind is C and "exported" in n.roles}
    if not exported_class_ids:
        return
    for n in nodes:
        if n.kind is M and not n.name.startswith(("_", "#")) \
                and n.id.rsplit(".", 1)[0] in exported_class_ids:
            n.roles = n.roles | {"exported"}


# -- pass 1: definitions ----------------------------------------------------
def _collect(node, src, rel, spec, lang, parent, nodes, defs, inherits, exported, is_test):
    for child in node.children:
        t = child.type
        if t == "export_statement":
            _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                     exported=True, is_test=is_test)
        elif t in spec.container_only:
            qual = _join(parent, _name_of(child, src))
            _collect(child, src, rel, spec, lang, qual, nodes, defs, inherits,
                     exported=False, is_test=is_test)
        elif t in spec.defs:
            name = _name_of(child, src)
            if not name:
                _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                         False, is_test)
                continue
            qual = _join(parent, name)
            roles = set(_roles(child, src, name, lang, exported))
            if _is_test_name(name):  # a test entry; other test-file helpers stay normal
                roles.add("test")
            kind = spec.defs[t]
            cid = f"{rel}::{qual}"
            nodes.append(Node(id=cid, kind=kind, name=name, location=_loc(rel, child),
                              end_line=child.end_point[0] + 1, roles=frozenset(roles)))
            defs.append((rel, cid, child, lang))
            if kind is C:
                for base in _bases(child, src, spec):
                    inherits.append((cid, base, lang))
            inner = qual if (t in spec.containers or kind is C) else parent
            _collect(child, src, rel, spec, lang, inner, nodes, defs, inherits,
                     False, is_test)
        elif spec.arrow_decls and t == "variable_declarator":
            val = child.child_by_field_name("value")
            name = _field_text(child, "name", src)
            if name and val and val.type in ("arrow_function", "function", "function_expression"):
                qual = _join(parent, name)
                roles = {"exported"} if exported else set()
                if _is_test_name(name):
                    roles.add("test")
                nodes.append(Node(id=f"{rel}::{qual}", kind=F, name=name,
                                  location=_loc(rel, val), end_line=val.end_point[0] + 1,
                                  roles=frozenset(roles)))
                defs.append((rel, f"{rel}::{qual}", val, lang))
        else:
            _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                     exported, is_test)


def _bases(node, src, spec):
    """Base class / interface names from a class def's heritage children."""
    names: list[str] = []
    for c in node.children:
        if c.type in spec.heritage:
            for leaf in _identifiers(c, src):
                names.append(leaf)
    return names


def _identifiers(node, src):
    out = []
    if node.type in ("identifier", "type_identifier", "name", "constant",
                     "scoped_type_identifier"):
        t = _trailing_id(node, src)
        if t:
            out.append(t)
        return out
    for c in node.children:
        out += _identifiers(c, src)
    return out


def _import_names(root, src, spec):
    if not spec.imports:
        return []
    names: list[str] = []
    def rec(n):
        if n.type in spec.imports:
            names.extend(_identifiers(n, src))
        for c in n.children:
            rec(c)
    rec(root)
    return names


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1].lower()
    parts = rel.lower().split("/")
    if "test" in parts or "tests" in parts or "spec" in parts:
        return True
    return ("_test." in name or ".test." in name or ".spec." in name
            or name.startswith("test_"))


def _is_test_name(name: str) -> bool:
    """A test *entry* by conventional naming (Go Test*/Benchmark*/Example*,
    pytest/JS test*). Other helpers in a test file are live only if a test
    reaches them — so they aren't blanket-marked."""
    return name[:4].lower() == "test" or name.startswith(("Benchmark", "Example"))


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
                     "property_identifier", "word", "name", "constant"):
        return _text(node, src)
    prop = node.child_by_field_name("property") or node.child_by_field_name("name")
    if prop is not None:
        return _trailing_id(prop, src)
    # A generic type (`Container<T>`, `Container::<T>`) names the *base* type — its
    # explicit `type` field — not the trailing type argument. Without this, an
    # `impl<T> Container<T>` block resolved to `T`, mis-attributing every method.
    base = node.child_by_field_name("type")
    if base is not None:
        return _trailing_id(base, src)
    for c in reversed(node.children):  # rightmost identifier-ish leaf
        # Skip type-argument/parameter subtrees so generics resolve to the base
        # name, not a parameter (e.g. the `T` inside `Container<T>`).
        if c.type in ("type_arguments", "type_parameters"):
            continue
        got = _trailing_id(c, src)
        if got:
            return got
    return None


def _ref(edges, src_id, name, by_name, rel, line, relation=Relation.CALLS):
    cands = [c for c in by_name.get(name, []) if c != src_id]
    loc = f"{rel}:{line}:0"
    if not cands:
        return
    if len(cands) == 1:
        edges.append(Edge(src=src_id, relation=relation, dst_symbol=name,
                          dst_id=cands[0], weight=1.0, provenance=Provenance.EXTRACTED,
                          location=loc, source="tree-sitter"))
    else:
        w = round(1.0 / len(cands), 3)
        for cid in cands:
            edges.append(Edge(src=src_id, relation=relation, dst_symbol=name,
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
    if name in ("main", "Main"):
        roles.add("main")
    if lang == "rust" and any(c.type == "visibility_modifier" for c in node.children):
        roles.add("exported")
    elif lang == "go" and name[:1].isupper():        # Go: capitalised = exported
        roles.add("exported")
    elif lang in ("java", "php", "csharp") and _has_public(node, src):
        roles.add("exported")
    return frozenset(roles)


def _has_public(node, src) -> bool:
    for c in node.children:
        if c.type in ("modifiers", "modifier", "visibility_modifier") \
                and "public" in _text(c, src):
            return True
        if c.type == "public":
            return True
    return False


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
