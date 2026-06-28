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

import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from ._testfile import is_test_file
from .python import SKIP_DIRS as _SKIP  # one shared dependency/build/VCS skip set

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
    HAS_TREE_SITTER = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_TREE_SITTER = False


def _load_grammar(lang: str):
    """Get a tree-sitter Language for `lang` using whatever pack is installed. The
    offline-default bundled line (<1.0) loads it directly; the download line (>=1.0)
    fetches on first use. If it's missing and the pack exposes an explicit `download()`
    (the 1.x API), fetch it once and retry — so "get it the easiest way available,
    including a runtime download when possible" holds even if auto-download is off
    (issue #12, Option 1). On the bundled line there's nothing to download, so a real
    failure propagates to the caller (surfaced as the issue-#7 warning)."""
    # The bundled line types `get_language(name: Literal[<all langs>])`; our `lang` is a
    # plain str (validated against SPECS), so cast to satisfy mypy across pack versions.
    name = cast(Any, lang)
    try:
        return get_language(name)
    except Exception:  # noqa: BLE001
        import tree_sitter_language_pack as _pack
        download = getattr(_pack, "download", None)
        if download is None:
            raise
        download([lang])  # may raise offline/proxied — propagate to the caller
        return get_language(name)

F, M, C = NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS

# Built-in base classes that don't trigger callback role (framework bases do).
# Languages with per-method visibility keywords (Go, Java, C#, PHP, Rust) handle
# visibility explicitly and don't need callback role seeding; only JS/TS and
# similar languages (where method visibility is inherited from class) use this.
_PLAIN_BASES = {
    # Built-in JS/TS value constructors — subclassing these is ordinary OOP, not a
    # framework-callback contract, so their methods stay dead-code eligible. Only
    # *plain* bases belong here: framework bases (React.Component, HTMLElement,
    # EventTarget, EventEmitter, …) must be ABSENT so their subclass methods get
    # the callback role — over-marking is the safe (precision-over-recall) direction.
    "Object", "Array", "Function", "Error", "TypeError", "RangeError",
    "SyntaxError", "Promise", "Map", "Set", "WeakMap", "WeakSet",
    "Symbol", "BigInt", "Number", "String", "Boolean", "Date", "RegExp",
}

# Methods a language runtime/framework invokes IMPLICITLY (never called by name in source),
# so the name-based call graph can't see the use — the cross-language analogue of skipping
# Python dunders and rooting C++ operator overloads/destructors. Rooting them (role
# 'callback') keeps the hook AND whatever its body calls live, instead of flagging the most
# important framework entry points dead (e.g. Ruby's `Sinatra::Base.inherited` — a class
# subclass hook and arguably sinatra's core mechanism — or Java's serialization `writeReplace`).
# Constructors are handled separately (`_CTOR_NAMES`) so they are NOT repeated here. Keyed by
# the raw file language. Only ever adds roots (cardinal-safe; over-rooting a genuinely-dead
# hook is the documented precision-over-recall trade-off).
_IMPLICIT_HOOKS: dict[str, frozenset[str]] = {
    "ruby": frozenset({
        "method_missing", "respond_to_missing?", "method_added", "method_removed",
        "method_undefined", "singleton_method_added", "singleton_method_removed",
        "singleton_method_undefined", "inherited", "included", "extended", "prepended",
        "append_features", "prepend_features", "extend_object", "initialize_copy",
        "initialize_clone", "initialize_dup", "coerce",
        "const_missing", "const_added",   # interpreter constant-resolution hooks (grape API)
        # Implicit conversion/coercion protocol — invoked by the interpreter on string
        # interpolation / `puts` / `p` (`to_s`/`inspect`), implicit coercion (`to_str`/`to_ary`/
        # `to_hash`/`to_int`/`to_io`/`to_path`), splat (`to_a`), double-splat (`to_h`), and
        # `&obj` block conversion (`to_proc`), and numeric coercion (`to_i`/`to_f`/`to_r` — note
        # `Float(obj)`/`Integer(obj)` emit a call to `Float`/`Integer`, NOT to the object's
        # `to_f`/`to_i`, so those hooks have no textual caller) — never by a textual call. The Ruby
        # analogue of Python's `__str__`/`__repr__` dunders (Ruby manual pass, cardinal).
        "to_s", "inspect", "to_str", "to_a", "to_ary", "to_h", "to_hash",
        "to_i", "to_int", "to_f", "to_r", "to_proc", "to_io", "to_path", "to_sym",
        # Enumerable / Comparable / Hash-key protocol — `each` is driven by every Enumerable
        # method (`map`/`select`/…); `<=>` (an operator, already rooted) drives Comparable;
        # `hash`/`eql?` are called by the interpreter when the object is a Hash key; `succ`
        # drives `Range#each`. Marshalling hooks (`marshal_dump`/`marshal_load`/`_dump`/`_load`)
        # are invoked by `Marshal.dump`/`.load` by name.
        "each", "each_pair", "hash", "eql?", "succ",
        "marshal_dump", "marshal_load", "_dump", "_load",
    }),
    "php": frozenset({
        "__destruct", "__call", "__callStatic", "__get", "__set", "__isset", "__unset",
        "__sleep", "__wakeup", "__serialize", "__unserialize", "__toString", "__invoke",
        "__set_state", "__clone", "__debugInfo",
    }),
    "java": frozenset({
        "equals", "hashCode", "toString", "finalize", "clone",
        "readObject", "writeObject", "readResolve", "writeReplace", "readObjectNoData",
        "readExternal", "writeExternal",
    }),
    "cpp": frozenset({
        # Range-based `for (x : r)` is desugared by the compiler to `r.begin()` / `r.end()` (or ADL
        # `begin(r)`/`end(r)`) — the name-based call graph never sees those calls, and no other pass
        # roots them, so an iterable's `begin`/`end` (+ whatever they reach) were false-flagged dead
        # (C++ manual pass, cardinal). A class defining `begin`/`end` is iterable by design, so
        # rooting them is semantically right; cardinal-safe over-rooting otherwise. Reaches `.cpp`/
        # `.cc`/`.cxx`/`.hpp` (raw lang `cpp`) AND any `.h` that `_header_lang` content-sniffs as C++
        # (it carries `class`/`namespace`/`template`/… markers); only a pure-C `.h` (no such markers,
        # raw lang `c`) keeps a `begin`/`end` dead-eligible — no C over-rooting.
        "begin", "end",
    }),
}


@dataclass(frozen=True)
class LangSpec:
    defs: dict[str, NodeKind]            # node type -> node kind (creates a node)
    call_types: dict[str, str | None]   # call node type -> field for the callee
    containers: frozenset[str] = frozenset()        # types whose children nest (qual)
    container_only: frozenset[str] = frozenset()    # nest qual but create no node
    arrow_decls: bool = False           # JS `const f = () => …`
    heritage: frozenset[str] = frozenset()          # child types holding base classes
    imports: frozenset[str] = frozenset()           # import statement node types
    bare_calls: bool = False            # Ruby: paren-less `foo` calls parse as identifier
    callable_strings: bool = False      # PHP: `[$this, 'method']` array callables
    attr_suffix: bool = False           # C#: `[Foo]` may resolve to class `FooAttribute`


_JS = LangSpec(
    defs={"function_declaration": F, "generator_function_declaration": F,
          "class_declaration": C, "method_definition": M},
    # `new Foo()` is a use of Foo: edge it so a class instantiated only via `new`
    # isn't false-flagged dead (Java/Python already model constructor calls).
    call_types={"call_expression": "function", "new_expression": "constructor"},
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
        call_types={"call_expression": "function", "new_expression": "type"},
        containers=frozenset({"class_specifier", "struct_specifier",
                              "field_declaration_list"}),
        heritage=frozenset({"base_class_clause"}),
    ),
    "csharp": LangSpec(
        defs={"method_declaration": M, "constructor_declaration": M,
              "class_declaration": C, "struct_declaration": C,
              "interface_declaration": C, "local_function_statement": F},
        call_types={"invocation_expression": "function",
                    "object_creation_expression": "type"},
        containers=frozenset({"class_declaration", "struct_declaration",
                              "interface_declaration", "declaration_list"}),
        heritage=frozenset({"base_list"}),
        imports=frozenset({"using_directive"}),
        attr_suffix=True,  # `[Foo]` may name class `FooAttribute` (C# omits the suffix)
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
        bare_calls=True,  # `validate` (no parens/receiver) is an idiomatic Ruby call
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
        callable_strings=True,  # `[$this, 'm']` / `[self::class, 'm']` array callables
    ),
}
EXT_LANG = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".rs": "rust", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".sh": "bash", ".bash": "bash", ".go": "go", ".java": "java", ".rb": "ruby",
    ".php": "php",
}


def supported_languages() -> list[str]:
    return sorted(set(EXT_LANG.values()))


def _canon_lang(lang: str) -> str:
    """Canonical resolution bucket for a language. C and C++ share one symbol namespace:
    real projects freely reference symbols across `.h`/`.c`/`.cpp`, and a `.h` may be parsed
    under either grammar — so binding *within* a single dialect leaves a header symbol used
    by the other dialect unresolved and flagged dead (panel UUU, cardinal). Merging C/C++ for
    name resolution only ever adds edges (precision-safe), never removes them."""
    return "cpp" if lang in ("c", "cpp") else lang


def _header_lang(path: Path) -> str:
    """Resolve an ambiguous `.h` header to C or C++ by content. The C grammar has no
    class/namespace/template node types, so a C++ header parsed as C mis-structures its
    classes and flags them dead (panel TTT, cardinal). Sniff for C++ markers and default to
    C, so pure-C headers are unaffected; over-accepting toward C++ is the precision-safe
    direction for the cardinal invariant."""
    try:
        head = path.read_bytes()[:16384]
    except OSError:
        return "c"
    # C++-only markers. Broad set (panel UUU): also catch access specifiers, virtual/operator/
    # nullptr and `extern "C++"`, so a struct-with-methods header routes to C++ and its member
    # functions are extracted. The C/C++ resolution buckets are unified, so a miss here is a
    # recall gap (methods not extracted), never a cardinal false-dead.
    markers = rb"\b(class|namespace|template|virtual|operator|nullptr|public|private|protected)\b|::"
    return "cpp" if re.search(markers, head) else "c"


class _DefInfo:
    """Streaming (cache_trees=False) substitute for a def's parse-tree body node.

    The tree-sitter extractor normally pins every file's parse tree (via the body refs
    in `defs`) AND every file's source bytes (`src_by`) across BOTH passes — Magento is
    PHP, so that double-pin is its actual memory hog. In streaming mode we precompute,
    while each file's tree is still alive in pass 1, the only things pass 2 and the seed
    passes ever read back from a body: its node type (`_iface_ids`), its call/ref tuples
    and C/C++ out-of-line scope (the edge loop), and the Rust trait-impl flag
    (`_seed_trait_impl_methods`). With those captured, the tree and source are freed
    per-file. `.type` mirrors the live node's `.type` so `_iface_ids` reads it unchanged.
    """

    __slots__ = ("type", "is_trait_impl", "calls", "refs", "cpp_scope", "cpp_line")

    def __init__(self, type, is_trait_impl, calls, refs, cpp_scope, cpp_line):  # noqa: A002
        self.type = type
        self.is_trait_impl = is_trait_impl
        self.calls = calls
        self.refs = refs
        self.cpp_scope = cpp_scope
        self.cpp_line = cpp_line


def _precompute_def(body, src, lang) -> _DefInfo:
    """Capture everything pass 2 / the seeds need from a live body node (cache_trees=False)
    — computed against the live tree + source so the streamed graph is byte-identical to the
    in-memory one (the streaming differential oracle is the gate)."""
    node = cast(Any, body)
    spec = SPECS[lang]
    cpp_scope = _cpp_method_scope(body, src) if lang in ("c", "cpp") else None
    is_trait_impl = False
    if lang == "rust" and node.type == "function_item":
        p = node.parent
        while p is not None:
            if p.type == "impl_item":
                is_trait_impl = p.child_by_field_name("trait") is not None
                break
            p = p.parent
    return _DefInfo(
        type=node.type,
        is_trait_impl=is_trait_impl,
        calls=_direct_calls(body, src, spec),
        refs=_direct_refs(body, src, spec),
        cpp_scope=cpp_scope,
        cpp_line=node.start_point[0] + 1,
    )


def extract(root: str | Path, ignore: list[str] | None = None, *,
            cache_trees: bool = True, edge_sink: Any = None) -> tuple[list[Node], list[Edge]]:
    # `cache_trees=False` is the streaming (lower-peak-memory) mode: each file's parse tree
    # and source are dropped after pass 1 (its defs' body refs are swapped for precomputed
    # `_DefInfo` records), so peak memory tracks symbol count, not total parse-tree size.
    # The result is byte-identical to the default path (test_streaming_differential.py).
    #
    # `edge_sink` (Phase 2b): an append-only object that consumes edges as they're produced
    # (streaming them straight to SQLite) instead of accumulating a Python list. Within this
    # function `edges` is WRITE-ONLY — no pass reads it back (override propagation is
    # Python-only, dedup happens in the store) — so a sink is a transparent drop-in. On a
    # Magento-scale PHP repo the edge list is the dominant hog (~15.5M edges); the sink
    # removes it from Python memory entirely. When a sink is given, the returned edge list is
    # empty (the edges already live in the sink/store).
    if not HAS_TREE_SITTER:
        return [], []
    root = Path(root)
    parsers: dict[str, Parser] = {}
    nodes: list[Node] = []
    defs: list[tuple[str, str, object, str]] = []  # (rel, def_id, body_node, lang)
    contains: list[tuple[str, str, str, int]] = []  # (func_id, nested_id, name, line)
    inherits: list[tuple[str, str, str]] = []      # (class_id, base_name, lang)
    imports: list[tuple[str, str, str]] = []       # (mod_id, name, lang)
    src_by: dict[str, bytes] = {}
    file_lang: dict[str, str] = {}
    reexports: set[str] = set()  # names from JS/TS `export { X }` clauses
    c_exports: dict[str, set[str]] = {}  # rel -> EXPORT_SYMBOL'd C function names (per file)
    c_decl_exports: set[str] = set()  # project-wide: names declared with an export attr (R77 F2)
    module_tests: list[tuple] = []  # (mod_id, rel, lang, calls, refs) for test files

    try:
        files = [p for p in sorted(root.rglob("*"))
                 if p.suffix in EXT_LANG and p.is_file() and _wanted(p, root, ignore)]
    except OSError:
        files = []  # unwalkable root (over-long path / permission) -> empty, not a crash
    grammar_failed: dict[str, int] = {}  # lang -> files skipped (grammar unavailable)
    for path in files:
        lang = EXT_LANG[path.suffix]
        if path.suffix == ".h":
            lang = _header_lang(path)  # .h is C or C++; the C grammar mis-parses C++ classes
        if lang not in SPECS:
            continue
        if lang in grammar_failed:  # already known-unavailable; don't retry, just count
            grammar_failed[lang] += 1
            continue
        if lang not in parsers:
            try:
                parsers[lang] = Parser(_load_grammar(lang))
            except Exception:  # noqa: BLE001 — grammar unavailable (download/build/version)
                # Do NOT swallow into a silent empty graph: a grammar that won't load
                # would otherwise make every file of that language vanish with no
                # signal (issue #7). Record it and warn once below.
                grammar_failed[lang] = 1
                continue
        try:
            src = path.read_bytes()
            tree = parsers[lang].parse(src)
        except (OSError, Exception):  # noqa: BLE001 — a malformed/unreadable single file
            continue
        rel = path.relative_to(root).as_posix()
        src_by[rel] = src
        file_lang[rel] = lang
        spec = SPECS[lang]
        mod_id = f"{rel}::{path.stem}"
        is_test = _is_test_file(rel)
        # In a test file the module node is itself a test entry root, and its
        # module-level calls (incl. those inside anonymous `test()`/`it()` callbacks)
        # are rooted from it (Bug B) — so call-based suites that define no named test
        # functions don't leave their helpers flagged dead.
        # A bash script's top-level body is its entry point (bash's __main__): seed the
        # module node as a root and root its top-level calls so a run-directly script
        # with no main() doesn't leave every function flagged dead (issue #22). A
        # function reached by nothing (incl. its own top level) still flags — intended.
        is_bash_script = lang == "bash"
        # C# top-level statements (the default `Program.cs` template since .NET 6) ARE the
        # program's Main entry point — like bash's top-level body and Python's __main__. A
        # `compilation_unit` with `global_statement` children is a top-level program; root it
        # as a script so its top-level calls / local functions aren't flagged dead (panel WWW).
        is_cs_toplevel = lang == "csharp" and any(
            c.type == "global_statement" for c in tree.root_node.children)
        # Ruby and PHP execute a file's top-level body every time it is required/loaded
        # (like bash's top-level body and C#'s top-level statements) — there is no
        # "definitions only, nothing runs" load mode the way an imported Python/JS module
        # has. So a module-level call in `app.rb` / `app.php` roots the function it
        # invokes; without this, top-level-only-used helpers in Ruby/PHP are flagged dead
        # — live code as dead, the cardinal sin (panel R33A, same class as bash #22 / C# WWW).
        is_exec_toplevel_lang = lang in ("ruby", "php")
        is_script = is_bash_script or is_cs_toplevel or is_exec_toplevel_lang
        mod_roles: set[str] = set()
        if is_test:
            mod_roles.add("test")
        if is_script:
            mod_roles.add("script")
        # Snapshot the mutable accumulators so a RecursionError mid-walk rolls the file
        # back cleanly — no orphan MODULE node / partial defs left behind (panel QQQ LOW;
        # mirrors the Python extractor's parsed-dict skip).
        _n0, _d0, _i0, _c0, _mt0, _im0 = (
            len(nodes), len(defs), len(inherits), len(contains),
            len(module_tests), len(imports))
        nodes.append(Node(id=mod_id, kind=NodeKind.MODULE, name=path.stem,
                          location=f"{rel}:1:0", roles=frozenset(mod_roles)))
        try:
            # Module-level calls/refs are captured for EVERY file, not just test/script
            # ones: top-level code runs when a module is loaded, so a symbol used only at
            # module scope (a registry value, dispatch-table entry, or instantiation) is
            # live whenever the module loads. The module node propagates this only when it
            # is itself a load root — the detector seeds a module that owns any root — so an
            # ordinary library module's edges don't over-root, but a class used only at the
            # top level of an exported module isn't flagged dead (panel R12, cardinal).
            calls, refs = _module_uses(tree.root_node, src, spec)
            if is_bash_script:
                # Commands that invoke a function via an ARGUMENT (trap HANDLER, complete -F
                # FUNC, export -f FUNC, time FUNC) — the generic command scan keys on the head
                # and misses these, so root the named functions too.
                calls = calls + _bash_callback_refs(tree.root_node, src)
            elif lang == "ruby":
                # Ruby idioms naming a method via a literal symbol the call graph can't see
                # (`xs.map(&:upcase)`, `enum_for(:m)`, `&method(:m)`) — root those methods too.
                calls = calls + _ruby_symbol_refs(tree.root_node, src)
            module_tests.append((mod_id, rel, lang, calls, refs))
            _collect(tree.root_node, src, rel, spec, lang, parent="", nodes=nodes,
                     defs=defs, inherits=inherits, exported=False, is_test=is_test,
                     contains=contains, enclosing_func=None)
            for name in _import_names(tree.root_node, src, spec):
                imports.append((mod_id, name, lang))
            reexports |= _reexport_names(tree.root_node, src)
            if _canon_lang(lang) == "cpp":  # C/C++: EXPORT_SYMBOL'd functions are public ABI
                names = _export_symbol_names(src) | _c_alias_target_names(src)
                if names:
                    c_exports[rel] = names
                # An export attribute on a *declaration* (commonly the header) must root the
                # matching out-of-line definition, which carries no attribute (panel R77 F2).
                # Project-wide because declaration and definition live in different files. Byte-gate
                # the AST walk to the rare files that mention an export attribute.
                if b"visibility" in src or b"dllexport" in src:
                    c_decl_exports |= _c_export_decl_names(tree.root_node, src)
        except RecursionError:
            # A pathologically deep tree (a huge flat expression in generated code)
            # overflows the recursive walk; skip the one file, never abort the whole
            # reindex (panel OOO, the tree-sitter analogue of the Python ast guard).
            del nodes[_n0:], defs[_d0:], inherits[_i0:]
            del contains[_c0:], module_tests[_mt0:], imports[_im0:]
            continue
        if not cache_trees:
            # Streaming: while THIS file's tree + source are still alive, precompute
            # everything pass 2 + the seed passes read back from each def, then drop the
            # tree (swap the body refs for `_DefInfo`) and the source (`src_by`). All of
            # `defs[_d0:]` belong to this file, so `src` is exactly `src_by[rel]`.
            for i in range(_d0, len(defs)):
                d_rel, d_id, d_body, d_lang = defs[i]
                defs[i] = (d_rel, d_id, _precompute_def(d_body, src, d_lang), d_lang)
            src_by.pop(rel, None)

    # Surface grammar-load failures instead of returning a silent empty graph (issue
    # #7): without this, a non-Python repo looks like "ran fine, found almost nothing".
    if grammar_failed:
        skipped = sum(grammar_failed.values())
        langs = ", ".join(sorted(grammar_failed))
        warnings.warn(
            f"tree-sitter grammar(s) unavailable for: {langs}; {skipped} file(s) "
            f"skipped and NOT analysed. The graph is incomplete for those languages. "
            f"Check the tree-sitter-language-pack install (offline/proxied environments "
            f"may fail to fetch grammars).",
            RuntimeWarning, stacklevel=2)

    # A named re-export (`export { Widget }`) marks its symbol as public API just like
    # `export class Widget` — without this the re-exported class/fn (and its methods)
    # are false-flagged dead (a precision gap with the inline-export and Python __all__
    # paths). Over-marking by name is the safe direction.
    if reexports:
        for n in nodes:
            # Guard by language: `export { X }` is JS/TS-only, so a same-named symbol in
            # an unrelated language (a dead Ruby `class Widget`) must not be marked exported
            # by a JS file's re-export (panel TTT LOW — cross-language false-negative).
            if n.kind in (C, F, M) and n.name in reexports \
                    and file_lang.get(n.id.split("::", 1)[0]) in _CLASS_VISIBILITY_LANGS:
                n.roles = n.roles | {"exported"}

    # C/C++ `EXPORT_SYMBOL(foo)` marks `foo` as public kernel/module ABI — called by code
    # outside this tree, so never dead for lack of an in-tree caller (the C analogue of
    # __all__ / module.exports; Linux hunt: 543 EXPORT_SYMBOL'd fns were flagged). Scoped to
    # the SAME file the macro appears in so a same-named static fn elsewhere isn't mis-rooted.
    if c_exports:
        for n in nodes:
            if n.kind in (F, M):
                rel = n.id.split("::", 1)[0]
                if n.name in c_exports.get(rel, ()):
                    n.roles = n.roles | {"exported"}

    # An export attribute on a C/C++ *declaration* (commonly a header) roots the matching
    # definition, whose out-of-line `.cpp` form carries no attribute (panel R77 F2). Project-wide
    # by name (declaration and definition are in different files) and scoped to C/C++ files — the
    # C/C++ analogue of __all__; cardinal-safe (over-roots a homonym only in the safe direction).
    if c_decl_exports:
        for n in nodes:
            if n.kind in (F, M) and n.name in c_decl_exports \
                    and _canon_lang(file_lang.get(n.id.split("::", 1)[0], "") or "") == "cpp":
                n.roles = n.roles | {"exported"}

    # Normalize in-class member functions to METHOD. C/C++ map every `function_definition`
    # to FUNCTION even for methods defined inside a class body (there is no separate
    # `method_declaration` node), so the method-based class-rooting passes below
    # (exported/test/callback/main/constructor) — which all key on kind METHOD — would skip
    # C++ methods and leave a live framework/entry class flagged dead (panels QQQ/RRR,
    # cardinal). A FUNCTION whose immediate parent is a class IS a method; reclassify it so
    # every rooting pass works for every language. The `.` + class-parent guard means a
    # free function (no dot) or a function nested in a method (parent is a method) is left
    # alone — only direct class members are promoted.
    _class_ids = {n.id for n in nodes if n.kind is C}
    if _class_ids:
        for n in nodes:
            if n.kind is F and "." in n.id and n.id.rsplit(".", 1)[0] in _class_ids:
                n.kind = M

    # C++ operator overloads (`operator+`, `operator[]`, conversion `operator bool`) and
    # destructors (`~Class`) are invoked IMPLICITLY — via operator syntax (`a + b`), scope
    # exit, or a conversion the call graph can't see without type inference. Root them (and
    # thus whatever their bodies call) rather than flag live code dead (panel R13A, cardinal);
    # the analogue of skipping Python dunders. Out-of-line operator defs are bare functions
    # (no class parent in the id), so cover both F and M.
    for n in nodes:
        if n.kind in (F, M) and _is_cpp_special_member(n.name) \
                and _canon_lang(file_lang.get(n.id.split("::", 1)[0], "") or "") == "cpp":
            n.roles = n.roles | {"callback"}

    # Ruby operator methods (`def []`, `def []=`, `def <=>`, `def ==`, `def <<`, `def +`, …) are
    # invoked through operator/index SYNTAX (`a[k]`, `a[k]=v`, `a <=> b`, `sort`, `a + b`), never
    # by a name the call scan sees — so once captured (their name node is `operator`, see
    # _trailing_id) they'd be flagged dead, and with them whatever their bodies use (panel R61,
    # grape: `def []=` constructs `ValueArray.new`). Root them as callback — the Ruby analogue of
    # the C++ special-member pass. An operator name never starts with a letter/underscore.
    for n in nodes:
        if n.kind in (F, M) and _is_ruby_operator_method(n.name) \
                and file_lang.get(n.id.split("::", 1)[0], "") == "ruby":
            n.roles = n.roles | {"callback"}

    # Language implicit hooks (Ruby `method_missing`/`inherited`/…, Java `writeReplace`/…,
    # PHP `__call`/…) — invoked by the runtime, never by name, so root them like the C++
    # special members above (panel: multi-language false-positive hunt, sinatra/gson).
    for n in nodes:
        if n.kind in (F, M):
            hooks = _IMPLICIT_HOOKS.get(file_lang.get(n.id.split("::", 1)[0], "") or "")
            if hooks and n.name in hooks:
                n.roles = n.roles | {"callback"}

    _seed_exported_class_methods(nodes, file_lang)
    # Public members of an exported interface/trait are public API but, unlike class
    # members, are implicitly public (no visibility token) so `_roles` never marks them
    # `exported` and the JS/TS-gated class pass above skips them — leaving a `pub trait` /
    # `public interface` default/abstract method flagged dead (panel SSS, cardinal). The
    # `defs` list keeps each def's AST node, so identify interface/trait containers by node
    # type and down-propagate `exported` to their non-private members.
    _iface_ids = {cid for _r, cid, body, _l in defs
                  if cast(Any, body).type in _INTERFACE_TYPES}
    _seed_exported_interface_methods(nodes, _iface_ids)
    _seed_classes_from_exported_methods(nodes)
    _seed_test_classes(nodes, inherits, file_lang)
    _seed_main_classes(nodes)
    _seed_trait_impl_methods(nodes, defs, cache_trees)

    # Resolve names *within a language* — a JS call must not bind to a Rust fn. C and C++
    # share one bucket (`_canon_lang`) so a header symbol resolves across the .h/.c/.cpp split.
    by_lang: dict[str, dict[str, list[str]]] = {}
    for n in nodes:
        _fl = file_lang.get(n.id.split("::", 1)[0])
        if _fl:
            by_lang.setdefault(_canon_lang(_fl), {}).setdefault(n.name, []).append(n.id)

    # Write-only accumulator: a real list, or the streaming sink (same append API).
    edges: Any = [] if edge_sink is None else edge_sink
    for rel, def_id, body, lang in defs:
        by_name = by_lang.get(_canon_lang(lang), {})
        # In streaming mode the body is a `_DefInfo` with the call/ref/scope tuples already
        # computed against the (now-freed) live tree; otherwise read them off the live body.
        if cache_trees:
            calls = _direct_calls(body, src_by[rel], SPECS[lang])
            refs = _direct_refs(body, src_by[rel], SPECS[lang])
            cpp_scope = _cpp_method_scope(body, src_by[rel]) if lang in ("c", "cpp") else None
            cpp_line = cast(Any, body).start_point[0] + 1
        else:
            info = cast(_DefInfo, body)
            calls, refs = info.calls, info.refs
            cpp_scope, cpp_line = info.cpp_scope, info.cpp_line
        called: set[str] = set()
        for name, line, is_method in calls:
            _ref(edges, def_id, name, by_name, rel, line, is_method=is_method)
            called.add(name)
        # Bare-name *references*: a symbol named by value/type (`const cb = handler`,
        # a class as the receiver of `Service.new` / `Color.RED`, a `new X()` class
        # name) is a real use the extractor sees. Edge it -> REFERENCES (only project
        # symbols resolve via `_ref`) so a live symbol used only by name isn't flagged
        # dead — closing the same gap the Python extractor's `_direct_names` does, and
        # covering constructor idioms whose grammar lacks a clean callee field.
        for name, line in refs:
            if name not in called:  # already a CALLS edge; don't double-count as REFERENCES
                _ref(edges, def_id, name, by_name, rel, line, relation=Relation.REFERENCES)
        # C/C++ out-of-line member definition `RetT Scope::method(...) {...}`: the method
        # body lives in a .cpp but its class is declared in a header, so the def is a bare
        # FUNCTION with no link to its class. Edge it -> REFERENCES its class (resolved by
        # name across the .h/.c/.cpp bucket) so a class whose members are all defined
        # out-of-line isn't flagged dead while a member is reached (panel R12B, cardinal).
        if lang in ("c", "cpp") and cpp_scope:
            _ref(edges, def_id, cpp_scope, by_name, rel, cpp_line, relation=Relation.REFERENCES)

    # Root module-level calls AND name-references of each test file from its module
    # node (Bug B): the `test()`->helper chain in call-based suites (Jest/Mocha/RSpec)
    # has no named test function to seed. The references half also roots a class used by
    # name as a call receiver (`Service.run` in an RSpec block) so the class isn't
    # flagged dead while its method is live (Panel FF). Mirrors the per-def loop above.
    for mod_id, rel, lang, calls, refs in module_tests:
        by_name = by_lang.get(_canon_lang(lang), {})
        called = set()
        for name, line in calls:
            _ref(edges, mod_id, name, by_name, rel, line)
            called.add(name)
        for name, line in refs:
            if name not in called:
                _ref(edges, mod_id, name, by_name, rel, line, relation=Relation.REFERENCES)

    # `function -> nested def` containment edges (see _collect): the nested def's id is
    # known exactly, so emit a direct REFERENCES edge rather than resolving by name. The
    # store's _dedup_edges subsumes this under a CALLS edge if the enclosing also calls
    # the nested def directly, so it never double-counts.
    for func_id, nested_id, name, line in contains:
        nrel = nested_id.split("::", 1)[0]
        edges.append(Edge(src=func_id, relation=Relation.REFERENCES, dst_symbol=name,
                          dst_id=nested_id, weight=1.0, provenance=Provenance.EXTRACTED,
                          location=f"{nrel}:{line}:0", source="tree-sitter"))

    # Class IDs that are framework (externally-subclassed) classes — their methods are
    # framework-invoked overrides, marked `callback` below.
    class_by_name: dict[str, set[str]] = {}
    for n in nodes:
        if n.kind is C:
            class_by_name.setdefault(n.name, set()).add(n.id)
    external_base_classes = _framework_classes(inherits, class_by_name)

    _seed_callback_roles(nodes, external_base_classes)

    for class_id, base, lang in inherits:
        _ref(edges, class_id, base, by_lang.get(_canon_lang(lang), {}),
             class_id.split("::", 1)[0], 0, relation=Relation.INHERITS)
    for mod_id, name, lang in imports:
        _ref(edges, mod_id, name, by_lang.get(_canon_lang(lang), {}),
             mod_id.split("::", 1)[0], 0, relation=Relation.IMPORTS)
    _seed_constructors(nodes, edges, file_lang)
    # A module node shares the id-space with same-named symbols: `run.sh::run` for a bash
    # script defining `run()`, `tests/Service.js::Service` for a test file defining class
    # Service. The store's INSERT OR REPLACE then drops the MODULE node — and with it the
    # module-only roles (`script`/`test`) that have no redundant assignment on the symbol
    # node — flagging the whole file's code dead (panels SSS/TTT, cardinal). Merge a
    # shadowed module node's roles into the surviving symbol node and drop the duplicate.
    _mod_by_id = {n.id: n for n in nodes if n.kind is NodeKind.MODULE}
    if _mod_by_id:
        _shadowed: set[str] = set()
        for n in nodes:
            if n.kind is not NodeKind.MODULE and n.id in _mod_by_id:
                n.roles = n.roles | _mod_by_id[n.id].roles
                _shadowed.add(n.id)
        if _shadowed:
            nodes = [n for n in nodes
                     if not (n.kind is NodeKind.MODULE and n.id in _shadowed)]
    # When streaming, the edges already live in the sink/store — return an empty list so the
    # combined extractor's `edges += je` is a no-op (the bulk edge list never materialises).
    return nodes, ([] if edge_sink is not None else edges)


# Languages where a method's visibility is inherited from its class (no per-method
# visibility token), so an exported class implies exported public methods. Languages
# that tokenize per-method visibility (Java/C#/Go/Rust/PHP) already get the correct
# per-method `exported` role from `_roles`, so seeding there would over-mark genuinely
# private methods as public API.
_CLASS_VISIBILITY_LANGS = {"javascript", "typescript", "tsx"}


def _seed_exported_class_methods(nodes, file_lang):
    """Public methods of an exported class are themselves public API — external
    callers reach them, so they're never dead for lack of an internal caller
    (precision over recall). Mirrors the Python extractor's `_apply_entrypoint_roles`.

    Only applies to languages where method visibility is inherited from the class
    (JS/TS); Java/C#/Go/Rust/PHP tokenize per-method visibility and already carry the
    correct `exported` role, so seeding there would hide genuinely-private dead code."""
    exported_class_ids = {n.id for n in nodes if n.kind is C and "exported" in n.roles}
    if not exported_class_ids:
        return
    for n in nodes:
        if n.kind is M and not n.name.startswith(("_", "#")) \
                and file_lang.get(n.id.split("::", 1)[0]) in _CLASS_VISIBILITY_LANGS \
                and n.id.rsplit(".", 1)[0] in exported_class_ids:
            n.roles = n.roles | {"exported"}


# Fixed-name constructor methods per language (Java/C#/C++ name the constructor
# after the class, handled separately via the class's own name).
_CTOR_NAMES = {
    "javascript": ("constructor",), "typescript": ("constructor",), "tsx": ("constructor",),
    "ruby": ("initialize",), "php": ("__construct",),
}


def _seed_constructors(nodes, edges, file_lang) -> None:
    """Link class -> constructor: constructing a class implicitly runs its
    constructor, so a class built only via `new X()` / `X.new` otherwise leaves the
    constructor (and whatever it constructs) unreachable -> false dead-code. Mirrors
    the Python extractor's class -> __init__ edge."""
    method_ids = {n.id for n in nodes if n.kind is M}
    for n in nodes:
        if n.kind is not C:
            continue
        lang = file_lang.get(n.id.split("::", 1)[0])
        for ctor in (*_CTOR_NAMES.get(lang, ()), n.name):  # n.name: class-named ctor
            mid = f"{n.id}.{ctor}"
            if mid in method_ids:
                edges.append(Edge(src=n.id, relation=Relation.REFERENCES, dst_symbol=ctor,
                                  dst_id=mid, weight=1.0, provenance=Provenance.EXTRACTED,
                                  location=n.location, source="tree-sitter"))


def _seed_classes_from_exported_methods(nodes) -> None:
    """Up-propagate `exported`: a class with an exported (public) method is itself
    public API. Languages with per-method visibility but no class-level export token
    — notably PHP, whose classes are implicitly public — otherwise seed the methods
    as roots while leaving the class flagged dead (the contradictory 'method live,
    class dead' shape). Over-marking the class as a root is the precision-safe
    direction."""
    class_ids = {n.id for n in nodes if n.kind is C}
    exported_classes = {n.id.rsplit(".", 1)[0] for n in nodes
                        if n.kind is M and "exported" in n.roles and "." in n.id
                        and n.id.rsplit(".", 1)[0] in class_ids}
    if not exported_classes:
        return
    for n in nodes:
        if n.id in exported_classes:
            n.roles = n.roles | {"exported"}


def _seed_test_classes(nodes, inherits, file_lang) -> None:
    """A class is a test fixture if it has a test-role method, *contains* a nested test
    class, or *inherits* its tests from a (custom) test base — mark all such classes
    `test` so the fixture isn't flagged dead while its tests are live (the
    'method/inner live, container dead' shape; Panel Z + AA). Over-marking a fixture is
    precision-safe; this only ever adds roots. Mirrors `_seed_classes_from_exported_methods`."""
    class_ids = {n.id for n in nodes if n.kind is C}
    test_classes = {n.id.rsplit(".", 1)[0] for n in nodes
                    if n.kind is M and "test" in n.roles and "." in n.id
                    and n.id.rsplit(".", 1)[0] in class_ids}
    if not test_classes:
        return
    # Resolve a base by (lang, name) — a same-named test class in another language must
    # NOT seed a production class here (tree-sitter resolves names within a language;
    # Panel BB finding 2). file_lang maps rel -> lang.
    name_to_ids: dict[tuple, list[str]] = {}
    for n in nodes:
        if n.kind is C:
            name_to_ids.setdefault((file_lang.get(n.id.split("::", 1)[0]), n.name), []) \
                .append(n.id)
    _grow_test_classes(test_classes, class_ids, inherits, name_to_ids)
    for n in nodes:
        if n.id in test_classes:
            n.roles = n.roles | {"test"}


def _grow_test_classes(test_classes: set, class_ids: set, inherits: list,
                       name_to_ids: dict) -> None:
    """Grow a seed set of test-class ids by (a) enclosing classes — a class that
    contains a nested test class is on the collection path — and (b) transitive
    inheritance — a subclass of a test base inherits its tests (the JUnit abstract-base
    + thin-subclass idiom). BOTH axes iterate to a single combined fixed point: a class
    discovered by inheritance may itself need its enclosing chain walked, and vice
    versa (Panel BB finding 1). In-place; monotonic (only adds) so it terminates."""
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
        for child_id, base_name, lang in inherits:
            if child_id not in test_classes and any(
                    bid in test_classes for bid in name_to_ids.get((lang, base_name), ())):
                test_classes.add(child_id)
                changed = True


# Interface/trait container node types whose members are implicitly public API.
_INTERFACE_TYPES = frozenset({
    "trait_item",            # rust
    "interface_declaration",  # java / c# / php
    "trait_declaration",      # php
})


def _seed_exported_interface_methods(nodes, interface_ids: set[str]) -> None:
    """Down-propagate `exported` from an exported interface/trait container to its
    non-private member methods. Interface/trait members are implicitly public (no visibility
    token), so `_roles`/`_has_public` never mark them exported, and `_seed_exported_class_methods`
    is gated to JS/TS — leaving Java/C#/Rust public interface members (incl. body-bearing
    default methods) flagged dead (panel SSS, cardinal). Over-rooting a rare explicitly-private
    interface member is the precision-safe direction."""
    exported_ifaces = {n.id for n in nodes
                       if n.kind is C and n.id in interface_ids and "exported" in n.roles}
    if not exported_ifaces:
        return
    for n in nodes:
        if n.kind is M and not n.name.startswith(("_", "#")) \
                and n.id.rsplit(".", 1)[0] in exported_ifaces:
            n.roles = n.roles | {"exported"}


def _seed_trait_impl_methods(nodes, defs, cache_trees=True) -> None:
    """Methods inside a Rust `impl Trait for X` block are public-by-contract API invoked via
    language sugar (operators, `Display::fmt` through `{}`, `Iterator::next` through `for`,
    `Drop::drop`) — no call node — and cannot carry `pub`, so `_roles` never marks them
    exported and they're flagged dead (panel UUU, cardinal). Root them as `callback`
    (framework/contract-invoked). A bare inherent `impl X` (no trait) is NOT rooted — only
    *trait* impls are public-by-contract."""
    impl_method_ids: set[str] = set()
    for _rel, cid, body, lang in defs:
        if lang != "rust":
            continue
        if not cache_trees:
            # Streaming: the trait-impl test was precomputed against the live tree.
            if body.type == "function_item" and body.is_trait_impl:
                impl_method_ids.add(cid)
            continue
        node = cast(Any, body)
        if node.type != "function_item":
            continue
        p = node.parent
        while p is not None:
            if p.type == "impl_item":
                if p.child_by_field_name("trait") is not None:
                    impl_method_ids.add(cid)
                break
            p = p.parent
    if not impl_method_ids:
        return
    for n in nodes:
        if n.id in impl_method_ids:
            n.roles = n.roles | {"callback"}


def _seed_main_classes(nodes) -> None:
    """An entry method (`main`/`Main` role) roots its enclosing class — otherwise an
    entry-point class whose only role-bearing member is the main method is flagged dead
    while its method is live. This bites idiomatic C# (`internal class Program { static
    void Main }`): `Main` isn't public so the class never gets the `exported` role, and no
    other pass roots it. The Python extractor has this rescue (`_seed_entrypoint_classes`);
    the tree-sitter side was missing it (panel RRR, cardinal). Precision-safe: only adds
    roots, only for classes that actually contain a `main`-role method."""
    main_classes = {
        n.id.rsplit(".", 1)[0] for n in nodes
        if n.kind is M and "main" in n.roles and "." in n.id
    }
    if not main_classes:
        return
    for n in nodes:
        if n.kind is C and n.id in main_classes:
            n.roles = n.roles | {"main"}


def _framework_classes(inherits, class_by_name: dict[str, set[str]]) -> set[str]:
    """Class ids that are framework (externally-subclassed) classes: those that (a) directly
    inherit an external base (resolves to no project class and isn't a plain base), plus (c)
    the transitive first-party descendants of such classes.

    A grandchild of a framework class is framework-driven the same way — its overrides of the
    framework's template methods are invoked polymorphically from unindexed framework code.
    Without the transitive step only the *direct* subclass got `callback` roots, so a deeper
    override (live, but with no in-tree caller) was flagged dead — CARDINAL. Confirmed on
    Magento (PHP template methods through an in-tree AbstractModel intermediary) and on a C#
    explicit `IDisposable.Dispose` reached via a project interface that extends the framework
    interface. Mirrors the Python extractor's `_apply_callback_roles` (cases (a) + (b) + (c)).

    Case (b) — same-name self-loop (`class Foo extends pkg.Foo`, base leaf binds to itself) —
    fires only when the base name resolves *solely* to the class itself (`class_by_name[base] ==
    {class_id}`). When the same short name also collides with an unrelated project class, this
    name-based check resolves the base to that distinct class instead, so the self-loop is not
    detected here; framework status then propagates only if the transitive closure reaches it.
    Closing that residual collision case needs the resolved INHERITS `dst_id` (python.py keys on
    `dst_id == src`); tracked as a follow-up. It is cardinal-safe in the common shapes and
    pre-existing — this change is a strict superset of the prior rooting (only ever adds).
    """
    external: set[str] = set()
    subclasses: dict[str, set[str]] = {}
    for class_id, base, _lang in inherits:
        # A base resolves first-party only if it names a *distinct* project class. A same-name
        # self-loop (`class Foo extends pkg.Foo` — the base leaf collides with the subclass and
        # binds to itself) is NOT a first-party base: it's the werkzeug-`EnvironBuilder` shape
        # where the real base is an external same-named framework class. Treat it as external
        # (case (b) in python.py's `_apply_callback_roles`) — otherwise a framework override on
        # such a class is flagged dead (cardinal).
        distinct = class_by_name.get(base, set()) - {class_id}
        if distinct:
            for pbase in distinct:
                subclasses.setdefault(pbase, set()).add(class_id)
        elif base not in _PLAIN_BASES:
            external.add(class_id)  # (a) direct external base, or (b) same-name self loop
    stack = list(external)
    while stack:  # (c) transitive closure down the first-party INHERITS tree (only adds)
        cid = stack.pop()
        for sub in subclasses.get(cid, ()):
            if sub not in external:
                external.add(sub)
                stack.append(sub)
    return external


def _seed_callback_roles(nodes, external_base_classes: set[str]) -> None:
    """Methods of a class with a framework base are framework-invoked overrides
    (e.g. React.Component.render, Express middleware). Mark them 'callback' so
    they're roots, not dead-code false positives (design §7 caveat, precision
    over recall). Mirrors the Python extractor's `_apply_callback_roles`."""
    if not external_base_classes:
        return
    classes_with_callbacks: set[str] = set()
    for n in nodes:
        if n.kind is M and "." in n.id:
            class_id = n.id.rsplit(".", 1)[0]
            if class_id in external_base_classes:
                n.roles = n.roles | {"callback"}
                classes_with_callbacks.add(class_id)
    # A framework subclass that overrides hook methods is framework-instantiated, so
    # mark the class a root too — otherwise the methods are live but the *class* is
    # flagged dead (the 'method live, class dead' cardinal false-dead; panel PPP, the
    # tree-sitter analogue of the Python `classes_with_callbacks` pass). Tie this to
    # *having* callback methods, not merely the base, so a bare unused subclass with no
    # overrides still flags.
    for n in nodes:
        if n.kind is C and n.id in classes_with_callbacks:
            n.roles = n.roles | {"callback"}


# Third-party Rust test-harness attribute paths whose last segment is not `test` (those ending in
# `::test` — tokio/async_std/test_log/googletest::test — are already matched generically).
_RUST_TEST_ATTR_PATHS = frozenset({"rstest", "test_case", "gtest", "quickcheck"})


def _is_rust_test_attr(attr_text: str) -> bool:
    """True for a Rust *test* attribute — matched on the attribute PATH, not a raw
    substring, so `#[cfg(feature="testing")]`, `#[doc="...test..."]`, a feature named
    `latest`, etc. do NOT count (issue #8, Panel W). Matches `#[test]`, `#[tokio::test]`
    / any `*::test`, and a bare `#[cfg(test)]` (incl. `cfg(all(test, ...))`)."""
    m = re.match(r"#!?\[\s*(.*?)\s*\]\s*$", attr_text.strip(), re.S)
    if not m:
        return False
    body = m.group(1)
    path = re.split(r"[(\s=]", body, maxsplit=1)[0].strip()  # attribute path before ( / = / ws
    if path == "test" or path.endswith("::test"):
        return True
    # Common third-party test-harness attributes whose path does NOT end in `test` — rstest
    # (`#[rstest]`), test-case (`#[test_case(...)]`), googletest-rust (`#[gtest]`), quickcheck
    # (`#[quickcheck]`). The free-form-named fn they decorate misses the test*/name convention,
    # so it (and its helpers) was flagged dead (documented recall gap, panel R84). Matched on the
    # last path segment so the crate-qualified form (`rstest::rstest`) is covered too. Cardinal-safe
    # (only adds test roots).
    if path.rsplit("::", 1)[-1] in _RUST_TEST_ATTR_PATHS:
        return True
    if path == "cfg" and "(" in body:
        # `cfg(test)` is test-gated; `cfg(feature="testing")` is not. Drop quoted string
        # values first (so a feature *value* containing "test" can't match), then drop
        # `not(...)` predicates — `cfg(not(test))` gates *production*-only code, so it
        # must NOT be marked a test (Panel CC) — then look for a bare `test` token.
        inner = re.sub(r"\"[^\"]*\"", "", body[body.find("(") + 1: body.rfind(")")])
        inner = re.sub(r"not\s*\([^)]*\)", "", inner)
        return "test" in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", inner)
    return False


def _is_rust_export_attr(attr_text: str) -> bool:
    """True for a Rust FFI/linker-export attribute — `#[no_mangle]` or `#[export_name = "…"]`.
    These export the function's symbol to the linker / foreign (C) code regardless of `pub`
    visibility, so the function is a public-ABI entry point with no in-tree caller (the Rust
    analogue of C's `EXPORT_SYMBOL`). Without this, a non-`pub` `#[no_mangle]` export — valid
    Rust, the symbol is still exported — has no `pub` to trigger export-rooting and is
    false-flagged dead along with whatever its body reaches (doc-driven, panel R69).

    Recognises the wrapped forms too: `#[unsafe(no_mangle)]` / `#[unsafe(export_name = "…")]`
    (REQUIRED syntax in the Rust 2024 edition — the bare form is an error there, so this is the
    mainstream spelling, not an edge case; panel R70) and `#[cfg_attr(<pred>, no_mangle)]`
    (conditionally applied — still a real export on the gated targets). Cardinal-safe: matching
    only ever *adds* an export root, so a broad match can over-root (mask dead code) but never
    flag live code dead. We therefore match the export token anywhere in the attribute content
    after dropping string-literal values, so `#[doc = "no_mangle"]` does NOT read as an export."""
    m = re.match(r"#!?\[\s*(.*?)\s*\]\s*$", attr_text.strip(), re.S)
    if not m:
        return False
    # Drop string-literal contents (`export_name = "sym"`, `doc = "…no_mangle…"`) so only
    # identifier tokens remain, then look for an export attribute path as a bare word. This
    # naturally covers the `unsafe(...)` (2024) and `cfg_attr(<pred>, …)` wrappers.
    inner = re.sub(r"\"(?:[^\"\\]|\\.)*\"", "", m.group(1))
    return bool(re.search(r"(?<![\w])(?:no_mangle|export_name)(?![\w])", inner))


# Rust attributes that mark a function as a RUNTIME entry point the language/runtime invokes
# automatically — never by an in-tree call, and (unlike `#[proc_macro]`, which requires `pub`)
# NOT necessarily `pub`, so export-rooting doesn't fire and the fn + its callees are flagged dead.
_RUST_RUNTIME_ENTRY_ATTRS = frozenset({
    "panic_handler", "start", "alloc_error_handler",
    # `ctor`/`dtor` crate: `#[ctor::ctor]` / `#[ctor]` / `#[ctor::dtor]` run a function
    # automatically before/after `main` — the direct Rust analogue of C `__attribute__((constructor))`
    # (which the C extractor already roots). Idiomatically *private*, so the `pub` safety net never
    # fires and the fn + its callees were false-flagged dead (Rust manual pass). Matched as a path
    # token, so `#[ctor::ctor]` and bare `#[ctor]` both hit; `#[constructor_helper]` does not.
    "ctor", "dtor",
})


def _is_rust_runtime_entry_attr(attr_text: str) -> bool:
    """True for a Rust runtime-entry attribute — `#[panic_handler]`, `#[start]`,
    `#[alloc_error_handler]`, or the `ctor`/`dtor` before/after-main attributes. The runtime (or the
    `ctor` crate's linker glue) calls these automatically, so a non-`pub` one has no in-tree caller
    and was false-flagged dead along with what its body reaches (doc-driven, panels R88/R96).
    Cardinal-safe: only adds roots.
    Matched on the attribute path (covers `#[unsafe(...)]` / `#[cfg_attr(...)]` wrappers via the
    last token), not a raw substring, so a `#[doc="…start…"]` can't trigger it."""
    m = re.match(r"#!?\[\s*(.*?)\s*\]\s*$", attr_text.strip(), re.S)
    if not m:
        return False
    inner = re.sub(r"\"(?:[^\"\\]|\\.)*\"", "", m.group(1))
    return any(re.search(rf"(?<![\w])(?:{a})(?![\w])", inner) for a in _RUST_RUNTIME_ENTRY_ATTRS)


# Annotation/attribute names that mark a method as a test entry or framework-invoked
# test hook (JUnit/TestNG, xUnit/NUnit/MSTest, PHPUnit). The analog of Rust `#[test]`:
# these decorate free-form-named methods that the test*/Test* name convention misses,
# so without detection the whole (often package-private) test class is flagged dead.
_TEST_ANNOTATIONS = {
    "java": frozenset({
        "Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate",
        "BeforeEach", "AfterEach", "BeforeAll", "AfterAll", "Nested", "Disabled",
        "Before", "After", "BeforeClass", "AfterClass",  # JUnit4 / TestNG
    }),
    "csharp": frozenset({
        "Fact", "Theory", "Test", "TestCase", "TestCaseSource", "TestMethod",
        "DataTestMethod", "SetUp", "TearDown", "OneTimeSetUp", "OneTimeTearDown",
        "TestInitialize", "TestCleanup", "ClassInitialize", "ClassCleanup",
        "TestFixture", "TestClass",  # class-level
    }),
    "php": frozenset({"Test", "DataProvider", "Before", "After", "BeforeClass", "AfterClass"}),
}

# Annotation/attribute names that mark a method as a FRAMEWORK CALLBACK — invoked by a
# runtime/container/serializer via reflection, never by name (the non-test analogue of
# `_TEST_ANNOTATIONS`). Grounded in the multi-language hunt: gson `@PostConstruct` (a real
# gson feature) and `@BeforeExperiment` (Caliper), Newtonsoft `[OnSerializing]`/
# `[OnDeserialized]`. Such a method — and whatever it calls — is a live entry point that the
# name-based call graph can't see. Marked `callback` (rooted). Only ever adds roots.
_CALLBACK_ANNOTATIONS = {
    "java": frozenset({
        "PostConstruct", "PreDestroy",                        # JSR-250 / CDI lifecycle
        "PrePersist", "PostPersist", "PreUpdate", "PostUpdate",
        "PreRemove", "PostRemove", "PostLoad",                # JPA entity lifecycle
        "EventListener", "Scheduled", "Bean",                 # Spring
        "Setup", "TearDown", "Benchmark",                     # JMH
        "BeforeExperiment", "AfterExperiment",                # Caliper
        "OnMethodEnter", "OnMethodExit",                      # ByteBuddy @Advice.* (instrumentation)
        "ToJson", "FromJson",                                 # Moshi adapter methods (reflection)
    }),
    "csharp": frozenset({
        "OnSerializing", "OnSerialized", "OnDeserializing",   # serialization callbacks
        "OnDeserialized", "OnError",
        "ModuleInitializer",                                  # runtime module init
        "GlobalSetup", "GlobalCleanup", "IterationSetup",     # BenchmarkDotNet
        "IterationCleanup", "Benchmark",
        "UnmanagedCallersOnly",                               # native (C-ABI) entry point
        "JSInvokable",                                        # Blazor JS interop
    }),
}

# JS/TS framework DECORATORS that mark a class as framework-instantiated or a method as a
# framework-invoked handler/callback (NestJS, Angular, TypeORM, routing-controllers). Unlike
# Java/C# annotations (children of the decl), TS decorators are a `decorator` node that is a
# CHILD of a `class_declaration` but a preceding SIBLING of a `method_definition` — so both
# positions are checked. A decorated class/method is reached by the framework, never by name,
# so it (and its callees) is a live root (multi-language hunt: nestjs controllers). Curated to
# well-known framework decorators so an ordinary decorator can't drag unrelated code live.
_CALLBACK_DECORATORS = frozenset({
    # HTTP route handlers (NestJS, routing-controllers)
    "Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All", "Search", "Sse",
    # NestJS class roots / microservices / websockets / GraphQL resolvers
    "Controller", "Injectable", "Module", "Resolver", "Catch", "WebSocketGateway",
    "SubscribeMessage", "MessagePattern", "EventPattern", "GrpcMethod", "GrpcStreamMethod",
    "Query", "Mutation", "ResolveField", "Subscription",
    # scheduling / events (NestJS schedule, event-emitter)
    "Cron", "Interval", "Timeout", "OnEvent",
    # Angular
    "Component", "Directive", "Pipe", "NgModule", "HostListener", "Input", "Output",
    # TypeORM / data-mapper class roots
    "Entity", "Repository", "EventSubscriber", "ChildEntity", "ViewEntity",
})


def _annotation_name(anno, src: str) -> str:
    """Last path segment of an annotation/attribute name, with C#'s optional
    `Attribute` suffix stripped (`[FactAttribute]` == `[Fact]`)."""
    nm = anno.child_by_field_name("name")
    if nm is None:
        for c in anno.children:
            if c.type in ("identifier", "scoped_identifier", "qualified_name",
                          "name", "member_access_expression"):
                nm = c
                break
    if nm is None:
        return ""
    txt = _text(nm, src).rsplit(".", 1)[-1].rsplit("\\", 1)[-1].strip()
    return txt[:-9] if txt.endswith("Attribute") else txt


def _annotation_idents(node, src: str) -> set[str]:
    """Collect annotation/attribute names attached to a Java/C#/PHP declaration —
    Java `modifiers > marker_annotation/annotation`, C# `attribute_list > attribute`,
    PHP `attribute_list > attribute_group > attribute`."""
    out: set[str] = set()
    for c in node.children:
        if c.type in ("modifiers", "attribute_list", "attribute_group"):
            out |= _annotation_idents(c, src)
        elif c.type in ("marker_annotation", "annotation", "attribute"):
            nm = _annotation_name(c, src)
            if nm:
                out.add(nm)
    return out


def _has_test_annotation(node, lang: str, src: str) -> bool:
    """True when a Java/C#/PHP declaration carries a test annotation/attribute — the
    cross-language analog of the Rust `#[test]` check (issue #8 generalised)."""
    annos = _TEST_ANNOTATIONS.get(lang)
    if not annos:
        return False
    return bool(_annotation_idents(node, src) & annos)


def _has_callback_annotation(node, lang: str, src: str) -> bool:
    """True when a Java/C# declaration carries a framework-callback annotation/attribute
    (`@PostConstruct`, `[OnSerializing]`, …) — reflection-invoked, never called by name."""
    annos = _CALLBACK_ANNOTATIONS.get(lang)
    if not annos:
        return False
    return bool(_annotation_idents(node, src) & annos)


def _decorator_name(deco, src: str) -> str:
    """Leaf name of a JS/TS `decorator` node: `@Get('x')` -> `Get`, `@ns.Controller()` ->
    `Controller`, `@Injectable` -> `Injectable`. Text-based so it handles call/member forms."""
    txt = _text(deco, src).lstrip("@").strip()
    txt = txt.split("(", 1)[0].strip()          # drop call arguments
    return txt.rsplit(".", 1)[-1].strip()       # last segment of a `ns.Name` path


def _has_callback_decorator(node, src: str, sibling_decos: list[str]) -> bool:
    """True when a JS/TS class/method carries a framework decorator (NestJS/Angular/TypeORM).
    A method's decorators precede it as SIBLINGS (passed in `sibling_decos`); a class's
    decorators are its own CHILDREN — check both positions."""
    names = set(sibling_decos)
    for c in node.children:
        if c.type == "decorator":
            names.add(_decorator_name(c, src))
    return bool(names & _CALLBACK_DECORATORS)


# -- pass 1: definitions ----------------------------------------------------
def _collect(node, src, rel, spec, lang, parent, nodes, defs, inherits, exported, is_test,
             contains, enclosing_func):
    pending_attrs: list[str] = []
    pending_decos: list[str] = []
    for child in node.children:
        t = child.type
        # Rust attributes (`#[test]`, `#[tokio::test]`, `#[cfg(test)]`, ...) parse as
        # sibling items preceding the def/mod they annotate; accumulate them so the
        # next real item can see them (issue #8).
        if t in ("attribute_item", "inner_attribute_item"):
            pending_attrs.append(_text(child, src))
            continue
        # JS/TS method decorators (`@Get('x')`) precede the method as SIBLINGS inside the
        # class body — accumulate them like Rust attributes so the next def can see them.
        if t == "decorator":
            pending_decos.append(_decorator_name(child, src))
            continue
        # A comment between a decorator/attribute and the def it annotates must NOT flush the
        # pending accumulators — `@Get()\n// note\nfindAll()` and `#[test]\n// note\nfn` are
        # common; resetting here drops the marker so the framework-callback/test rooting never
        # fires and the live handler (and its callees) is flagged dead (panel R40B/R41A).
        # Cover every supported grammar's comment node type: most use `comment`; Rust uses
        # `line_comment`/`block_comment` (Rust-only among supported langs — and it is the one
        # language using the sibling `attribute_item` accumulator, so missing it dropped
        # `#[test]` markers).
        if t in ("comment", "line_comment", "block_comment"):
            continue
        attrs, pending_attrs = pending_attrs, []
        decos, pending_decos = pending_decos, []
        attr_test = any(_is_rust_test_attr(a) for a in attrs)
        attr_export = any(_is_rust_export_attr(a) for a in attrs)
        attr_runtime = any(_is_rust_runtime_entry_attr(a) for a in attrs)
        if t == "export_statement":
            _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                     exported=True, is_test=is_test, contains=contains,
                     enclosing_func=enclosing_func)
        elif t in spec.container_only:
            qual = _join(parent, _name_of(child, src))
            # A Rust `impl Trait for Type` block means `Type` satisfies `Trait` — emit an
            # INHERITS Type -> Trait edge (resolved by name) so a private trait whose method
            # is reached but whose name never appears in a reachable body isn't flagged dead
            # (panel R16A, cardinal; the analogue of Ruby `include Module`). `impl Type` with
            # no trait is inherent (no edge).
            if t == "impl_item" and qual:
                _tr = child.child_by_field_name("trait")
                _trn = _trailing_id(_tr, src) if _tr is not None else None
                if _trn:
                    inherits.append((f"{rel}::{qual}", _trn, lang))
            _collect(child, src, rel, spec, lang, qual, nodes, defs, inherits,
                     exported=False, is_test=is_test, contains=contains,
                     enclosing_func=enclosing_func)
        elif t in spec.defs:
            # A bodyless C/C++ struct/union/enum/class specifier is a TYPE REFERENCE, not a
            # definition: `struct timeval tv` (a param/field/local), a forward decl
            # `struct X;`, an `enum E` used as a type. It has no `body` field. Extracting it
            # as a CLASS mints a phantom node that is then flagged dead (hiredis: `struct
            # timeval`/`struct event_base` references became dozens of dead "classes"). Only a
            # specifier WITH a body defines a type; descend so any real nested defs still get
            # collected (a bodyless ref has none — keeps the traversal uniform).
            if t.endswith("_specifier") and child.child_by_field_name("body") is None:
                _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                         False, is_test, contains=contains, enclosing_func=enclosing_func)
                continue
            name = _name_of(child, src)
            if not name:
                _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                         False, is_test, contains=contains, enclosing_func=enclosing_func)
                continue
            qual = _join(parent, name)
            roles = set(_roles(child, src, name, lang, exported))
            # A test entry-point root by name convention (test*/Benchmark*/Example*)
            # or by a Rust `#[test]`/`#[tokio::test]` attribute. The attribute case
            # fixes idiomatic Rust inline unit tests, whose names are free-form
            # (`#[cfg(test)] mod tests { fn closeness_works() {...} }`) so the name
            # convention never fires — they (and the helpers they *reach*) were flagged
            # dead, flooding find_stale (issue #8). A test helper reached by no test
            # stays flagged, consistent with a dead helper in any test file.
            if _is_test_name(name) or attr_test or _has_test_annotation(child, lang, src):
                roles.add("test")
            # A Rust `#[no_mangle]`/`#[export_name]` fn is a linker/FFI export — public ABI with
            # no in-tree caller — so root it even without `pub` (the Rust analogue of C
            # EXPORT_SYMBOL; doc-driven panel R69).
            if attr_export:
                roles.add("exported")
            # A Rust `#[panic_handler]`/`#[start]`/`#[alloc_error_handler]` fn is invoked by the
            # runtime, not by an in-tree call, and need not be `pub` — root it (panel R88).
            if attr_runtime:
                roles.add("callback")
            # A method carrying a framework-callback annotation (@PostConstruct,
            # [OnSerializing], …) is reflection-invoked — root it (multi-language hunt).
            if _has_callback_annotation(child, lang, src):
                roles.add("callback")
            # JS/TS: a class/method carrying a framework decorator (@Controller/@Get/@Entity)
            # is framework-instantiated/-invoked, never called by name — root it.
            if lang in ("javascript", "typescript", "tsx") \
                    and _has_callback_decorator(child, src, decos):
                roles.add("callback")
            kind = spec.defs[t]
            cid = f"{rel}::{qual}"
            nodes.append(Node(id=cid, kind=kind, name=name, location=_loc(rel, child),
                              end_line=child.end_point[0] + 1, roles=frozenset(roles)))
            defs.append((rel, cid, child, lang))
            if kind is C:
                for base in _bases(child, src, spec):
                    inherits.append((cid, base, lang))
            # A def nested inside a *function/method* (not a class or module) is live
            # iff its enclosing function is reachable — it executes, is returned as a
            # closure, or registered when the enclosing runs. Edge enclosing -> nested
            # so it isn't false-flagged dead, mirroring the Python extractor's
            # `function -> nested` containment edge (Panel Q). Only function scopes get
            # this: class/module scopes must NOT auto-reach their members — finding the
            # dead ones is dead-code's whole job.
            if enclosing_func is not None:
                contains.append((enclosing_func, cid, name, child.start_point[0] + 1))
            # Nest *every* def's children under its own qual (functions too, not only
            # classes/containers), so a function-local def becomes `outer.inner`, not a
            # module-scope `inner` that collides with a same-named sibling and merges
            # two distinct functions into one node. Matches Python's nested quals.
            child_func = cid if kind in (F, M) else None
            _collect(child, src, rel, spec, lang, qual, nodes, defs, inherits,
                     False, is_test, contains=contains, enclosing_func=child_func)
        elif spec.arrow_decls and t == "variable_declarator":
            # Peel TS value wrappers up front (`as const`/`as T`/`satisfies T`/`(…)`) so a
            # wrapped arrow/function/object value is still modeled — `export const f =
            # (() => helper()) as any` otherwise never becomes a node and `helper` is flagged
            # dead (cardinal). Applies uniformly to the arrow/function and the object branch.
            val = _unwrap_ts_value(child.child_by_field_name("value"))
            name = _field_text(child, "name", src)
            if name and val and val.type in ("arrow_function", "function", "function_expression", "generator_function"):
                qual = _join(parent, name)
                roles = {"exported"} if exported else set()
                if _is_test_name(name):
                    roles.add("test")
                cid = f"{rel}::{qual}"
                nodes.append(Node(id=cid, kind=F, name=name,
                                  location=_loc(rel, val), end_line=val.end_point[0] + 1,
                                  roles=frozenset(roles)))
                defs.append((rel, cid, val, lang))
                if enclosing_func is not None:
                    contains.append((enclosing_func, cid, name, child.start_point[0] + 1))
                # Recurse into the arrow/function-expression body so defs nested inside
                # it become real nodes with a containment edge — without this, a def in
                # an arrow (`const h = () => { function w(){...} }`, pervasive in JS/TS)
                # is never modeled and a symbol used only there is flagged dead (the
                # regular-def branch above already does this; this is its arrow twin).
                _collect(val, src, rel, spec, lang, qual, nodes, defs, inherits,
                         False, is_test, contains=contains, enclosing_func=cid)
            elif name and val is not None and val.type == "object":
                # `const obj = { run() {…}, h: () => {…} }` (also `{…} as const` / `satisfies T`,
                # already unwrapped above) — extract the object's function-valued members so
                # their bodies are walked (else a helper called only there is flagged dead;
                # cardinal). See _object_members.
                _object_members(val, src, rel, spec, lang, _join(parent, name),
                                nodes, defs, inherits, contains, is_test, enclosing_func)
            elif name and val is not None and val.type in ("class", "class_expression"):
                # `export const Widget = class extends Base { render() {…} }` — a class
                # expression bound to a const. The declarator branch handled arrow/fn/object
                # but not `class`, so the class was never a node and its methods' callees were
                # flagged dead (cardinal, #80). Mirror the assignment_expression class branch:
                # model it as a CLASS, emit INHERITS edges, walk the body. An `export`ed const
                # class takes the `exported` role so `_seed_exported_class_methods` rescues its
                # public methods (private methods stay dead-eligible, R46A); a non-exported one
                # is reached by name / `new X()` like any class. The body recursion gates the
                # methods to the class when nested in a function (round-3/4 rule), else None
                # (module scope — exported rescue / call resolution).
                qual = _join(parent, name)
                roles = {"exported"} if exported else set()
                if _is_test_name(name):
                    roles.add("test")
                cid = f"{rel}::{qual}"
                nodes.append(Node(id=cid, kind=C, name=name, location=_loc(rel, val),
                                  end_line=val.end_point[0] + 1, roles=frozenset(roles)))
                defs.append((rel, cid, val, lang))
                for base in _bases(val, src, spec):
                    inherits.append((cid, base, lang))
                if enclosing_func is not None:
                    contains.append((enclosing_func, cid, name, child.start_point[0] + 1))
                _collect(val, src, rel, spec, lang, qual, nodes, defs, inherits,
                         False, is_test, contains=contains,
                         enclosing_func=(cid if enclosing_func is not None else None))
            else:
                # The value wraps an object/class literal in an EXPRESSION shape:
                # `const x = f({…})` (call arg), `[ {…} ]` (array), `cond ? {…} : y` (ternary),
                # `a || {…}` / `a ?? {…}` (logical), `(() => ({…}))()` (IIFE),
                # `const routes = m.exports = {…}` (chained/parenthesized assignment). The
                # declarator previously SWALLOWED these (no descent), so the inner literal was
                # never reached and a helper called only from its members was flagged dead (#75,
                # cardinal). Descend generically so the literal is reached — this is now SAFE
                # because the main-loop `object`/`class_expression` interception routes any
                # literal it finds through proper member rooting, instead of letting raw descent
                # mint its method_definitions as UNROOTED module-scope nodes (the round-11
                # cardinal the old no-else guarded against, before that interception existed).
                _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                         exported, is_test, contains=contains, enclosing_func=enclosing_func)
        elif spec.arrow_decls and t == "assignment_expression":
            # A function/class assigned to an object MEMBER — `app.render = function(){…}`
            # (Express/CommonJS prototype augmentation), `Foo.prototype.m = () => {…}`,
            # `module.exports.x = function(){…}`, `this.handler = function(){…}`. Unlike a
            # `const f = function(){}` declaration (handled above), this never became a node,
            # so its BODY was never walked and the calls inside it were invisible — a
            # module-private helper it alone calls (Express `tryRender`/`logerror`, jQuery
            # internals) was then flagged dead. Model it and walk the body.
            left = child.child_by_field_name("left")
            # Peel TS value wrappers on the RHS (`obj.X = (class {…}) satisfies T`,
            # `obj.f = (() => …) as any`) before the type check — else a wrapped function/class
            # assignment is dropped and a helper it alone calls is flagged dead (cardinal). The
            # object-literal RHS path below is unwrapped too (via the variable_declarator-style
            # walrus on `_unwrap_ts_value`).
            val = _unwrap_ts_value(child.child_by_field_name("right"))
            prop = left.child_by_field_name("property") if left is not None \
                and left.type == "member_expression" else None
            name = _text(prop, src) if prop is not None else None
            if name and val is not None and val.type in (
                    "arrow_function", "function", "function_expression",
                    "generator_function", "class", "class_expression"):
                qual = _join(parent, _text(left, src))   # full LHS keeps ids distinct
                kind = C if val.type in ("class", "class_expression") else M
                roles = {"exported"} if exported else set()
                if _is_test_name(name):
                    roles.add("test")
                # A member-assigned function/class at MODULE/class scope is a method/handler/
                # export invoked externally or dynamically (a prototype method, a route
                # handler, an export), never by a plain local name — root it (callback) unless
                # underscore-private. Gated to `enclosing_func is None`: an assignment nested
                # inside a function body (`function init(){ obj.x = fn }`, `this.x = fn` in a
                # constructor) is NOT externally visible unless that function runs, so it must
                # stay reachability-gated via the CONTAINS edge below — else a dead initializer
                # would mint live roots and mask its own dead members (panel R40C). Only ever
                # adds roots at module scope (cardinal-safe). A member-assigned CLASS
                # (`exports.Parser = class {…}`) is public API, so it takes the `exported`
                # role — NOT `callback` — so that `_seed_exported_class_methods` rescues its
                # public methods too; otherwise the class is live via the root while its
                # methods (and their private callees) are flagged dead, the inverse-cardinal
                # "class live, methods dead" shape (panel R46A). A function/handler keeps
                # `callback`.
                if not name.startswith("_") and enclosing_func is None:
                    roles.add("exported" if kind is C else "callback")
                cid = f"{rel}::{qual}"
                nodes.append(Node(id=cid, kind=kind, name=name, location=_loc(rel, val),
                                  end_line=val.end_point[0] + 1, roles=frozenset(roles)))
                defs.append((rel, cid, val, lang))
                if kind is C:
                    for base in _bases(val, src, spec):
                        inherits.append((cid, base, lang))
                if enclosing_func is not None:
                    contains.append((enclosing_func, cid, name, child.start_point[0] + 1))
                # Same containment rule as the object-literal member path: a member-assigned
                # CLASS nested in a FUNCTION (`function f(){ obj.X = class { run(){…} } }`) is not
                # exported-rooted — it's gated to the enclosing fn via the CONTAINS edge above —
                # so its methods must be gated to the CLASS (cid), or they are orphaned and
                # confidently flagged dead while live (cardinal; panel round 4). A module-scope
                # member-assigned class keeps enclosing_func=None so the `exported` rescue leaves
                # its private methods dead-eligible (R46A). A function value (kind M) is cid-gated.
                _collect(val, src, rel, spec, lang, qual, nodes, defs, inherits,
                         False, is_test, contains=contains,
                         enclosing_func=(cid if (kind is M or enclosing_func is not None)
                                         else None))
            elif left is not None and val is not None and val.type == "object":
                # An object literal assigned to a member or name — `module.exports = {…}`,
                # `exports = {…}`, `Foo.prototype = { m(){…} }` (also `{…} as const`, already
                # unwrapped above). Extract
                # its function-valued members so their bodies are walked (else a helper called
                # only there is flagged dead; cardinal). The full LHS keeps the qual unique.
                _object_members(val, src, rel, spec, lang, _join(parent, _text(left, src)),
                                nodes, defs, inherits, contains, is_test, enclosing_func)
            else:
                _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                         exported, is_test, contains=contains, enclosing_func=enclosing_func)
        elif spec.arrow_decls and t == "object":
            # An object literal reached via generic descent — i.e. in an EXPRESSION position
            # (call argument, array element, ternary/logical branch, IIFE return, sequence,
            # parenthesized/chained-assignment RHS), NOT as a `const`/member VALUE (those are
            # consumed by the declarator/assignment branches above, which never descend here).
            # Route it through _object_members so its function-valued members are ROOTED
            # (callback/exported at module scope, CONTAINS-gated inside a function body) and
            # their bodies walked — else raw descent would mint the method_definitions as
            # UNROOTED module-scope nodes (the live method itself flagged dead — the round-11
            # cardinal) and a helper called only from a member would be flagged dead (#75,
            # cardinal). A position-synthesized qual keeps the anonymous object's members from
            # colliding with a same-named real module function.
            _object_members(child, src, rel, spec, lang,
                            _join(parent, f"<obj@{child.start_point[0] + 1}_{child.start_point[1]}>"),
                            nodes, defs, inherits, contains, is_test, enclosing_func)
        elif spec.arrow_decls and t in ("class", "class_expression") \
                and child.child_by_field_name("body") is not None:
            # An anonymous/expression-position class literal (`reg(class {…})`, `[ class {…} ]`,
            # `cond ? class {…} : null`). The `body`-field guard skips the bare `class` KEYWORD
            # token (also type "class", but bodyless) that sits inside every class_declaration —
            # without it, descending into a regular `class X {}` would mint a spurious
            # `X.<class@…>` node that masks X (the class would never flag dead). Named class
            # expressions bound to a const/member are
            # consumed by the declarator/assignment branches and never reach here. Model it as a
            # CLASS and walk its body so its methods (and their private callees) aren't flagged
            # dead — same round-11 reasoning as the object case. At module scope it takes the
            # `exported` role so `_seed_exported_class_methods` rescues its public methods (a
            # class handed to a function is instantiated/invoked externally); nested in a
            # function it is reachability-gated via CONTAINS and its methods gated to the class
            # (round-3/4 rule). A position-synthesized qual keeps it uniquely ided.
            qual = _join(parent, f"<class@{child.start_point[0] + 1}_{child.start_point[1]}>")
            roles = {"exported"} if enclosing_func is None else set()
            cid = f"{rel}::{qual}"
            nodes.append(Node(id=cid, kind=C, name="<anonymous>", location=_loc(rel, child),
                              end_line=child.end_point[0] + 1, roles=frozenset(roles)))
            defs.append((rel, cid, child, lang))
            for base in _bases(child, src, spec):
                inherits.append((cid, base, lang))
            if enclosing_func is not None:
                contains.append((enclosing_func, cid, "<anonymous>", child.start_point[0] + 1))
            _collect(child, src, rel, spec, lang, qual, nodes, defs, inherits,
                     False, is_test, contains=contains,
                     enclosing_func=(cid if enclosing_func is not None else None))
        else:
            _collect(child, src, rel, spec, lang, parent, nodes, defs, inherits,
                     exported, is_test, contains=contains, enclosing_func=enclosing_func)


_OBJ_FN_VALUES = ("arrow_function", "function", "function_expression", "generator_function")
_TS_VALUE_WRAPPERS = ("as_expression", "satisfies_expression", "parenthesized_expression")


def _unwrap_ts_value(val):
    """Peel TypeScript expression wrappers that sit between a `variable_declarator` value
    and the object/function it actually holds: `{…} as const`, `{…} as T`, `{…} satisfies T`
    (TS 4.9+), and `({…})`. The inner expression is the first NAMED child. Without this the
    `val.type == "object"` check misses `export const obj = {…} as const` — pervasive in
    modern TS — leaving the object untraversed and a helper called only from its members
    flagged dead (cardinal)."""
    seen = 0
    while val is not None and val.type in _TS_VALUE_WRAPPERS and seen < 8:
        val = val.named_child(0)
        seen += 1
    return val


def _obj_key_name(key, src):
    """The static name of an object-literal member key, or None. A plain
    `property_identifier`/`identifier` is its text; a string key (`"on-click"() {…}`,
    `"k": fn`) is the unquoted fragment — `_name_of` returns None for a string-keyed
    method, which would silently drop the member and leave its body unwalked (cardinal).
    A computed key (`[Symbol.iterator]`, `[expr]`) or number key has no static name → None
    (computed-symbol dispatch is its own concern)."""
    if key is None:
        return None
    if key.type == "string":
        frag = next((c for c in key.children if c.type == "string_fragment"), None)
        return _text(frag, src) if frag is not None else None
    if key.type in ("property_identifier", "identifier", "private_property_identifier"):
        return _text(key, src)
    return None


def _object_members(obj, src, rel, spec, lang, parent, nodes, defs, inherits, contains,
                    is_test, enclosing_func):
    """Extract the function-valued members of a JS/TS object literal as METHOD nodes so
    their bodies are walked in pass 2 — without this, a module-private function called
    ONLY inside `const obj = { run() { helper() } }` (method shorthand) or
    `{ run: () => helper() }` (function-valued property) is flagged dead, because the
    object value is never traversed and the call to `helper` is never seen (cardinal;
    rxjs/lodash-style config objects). A member at MODULE scope is invoked dynamically —
    spread into config, passed as a callback, or looked up by a (often computed/string)
    key — never by a plain local name, so root it `callback`. This rooting is
    UNCONDITIONAL at module scope (including underscore-`_private` and computed-key
    members): object literals are the canonical dispatch-table idiom (`handlers[evt]()`,
    `handlers["_" + name]()`), so an underscore or computed member is just as likely to be
    reached dynamically as a public one — gating it out would mint an UNROOTED node that is
    then confidently flagged dead while live (the cardinal sin; this is why object literals
    differ from the `assignment_expression` member gate, where the member is named
    statically and resolves by name). Over-rooting a genuinely-dead member is the
    precision-over-recall, cardinal-safe direction. A member nested in a function body
    instead stays reachability-gated via the CONTAINS edge below — a dead initializer must
    not mint live roots. Recurses into nested object values so `{ a: { onClick(){…} } }` is
    covered too."""
    for child in obj.children:
        kind = M
        if child.type == "method_definition":
            key = child.child_by_field_name("name")
            val = child
        elif child.type == "pair":
            key = child.child_by_field_name("key")
            # Peel TS wrappers on the MEMBER VALUE too (`run: (() => h())`,
            # `run: (fn satisfies T)`, `run: ({…} as const)`) — not just the whole object —
            # or a wrapped function/object member is dropped and its body never walked (cardinal).
            val = _unwrap_ts_value(child.child_by_field_name("value"))
            if val is None:
                continue
            if val.type == "object":
                # Recurse into a nested object value; a computed/string key still yields a
                # parent qual (the raw key text) so deeper members stay uniquely ided.
                nm = _obj_key_name(key, src) or (_text(key, src) if key is not None else None)
                if nm:
                    _object_members(val, src, rel, spec, lang, _join(parent, nm),
                                    nodes, defs, inherits, contains, is_test, enclosing_func)
                continue
            if val.type in ("class", "class_expression"):
                # A class-valued member (`{ Parser: class {…} }`) is public API — model it as a
                # CLASS and recurse its body so its methods (and their private callees) aren't
                # flagged dead; it takes the `exported` role so `_seed_exported_class_methods`
                # rescues its public methods (mirrors the assignment_expression branch, R46A).
                kind = C
            elif val.type not in _OBJ_FN_VALUES:
                continue
        else:
            continue
        name = _obj_key_name(key, src)
        if not name:
            # A computed/dynamic key (`[k]() {}`, `[Symbol.x]: () => …`) has no static name,
            # but its body must still be walked or a helper called only there is flagged dead
            # (cardinal). Synthesize an id from the key text; a computed-key member is
            # inherently accessed dynamically, so it is rooted below at module scope.
            name = _text(key, src) if key is not None else "[computed]"
        qual = _join(parent, name)
        roles: set[str] = set()
        # Module scope (not nested in a function body): dynamically invoked, root it
        # unconditionally (see the docstring — underscore/computed members included). A class
        # member takes `exported` (public API → its public methods are rescued); a function/
        # method member takes `callback`.
        if enclosing_func is None:
            roles.add("exported" if kind is C else "callback")
        cid = f"{rel}::{qual}"
        nodes.append(Node(id=cid, kind=kind, name=name, location=_loc(rel, val),
                          end_line=val.end_point[0] + 1, roles=frozenset(roles)))
        defs.append((rel, cid, val, lang))
        if kind is C:
            for base in _bases(val, src, spec):
                inherits.append((cid, base, lang))
        if enclosing_func is not None:
            contains.append((enclosing_func, cid, name, child.start_point[0] + 1))
        # Walk the member BODY so a def nested inside it (`run() { function inner(){…} }`)
        # becomes a real node with a CONTAINS edge to this member — without this, pass 2's
        # `_direct_calls` skips the nested def, its body is never walked, and a helper called
        # only from it is flagged dead (cardinal). Mirrors the arrow-decl path's body recursion.
        # Containment for the recursion:
        #   * method member (kind M): gate its nested defs to the method (cid).
        #   * class member at MODULE scope (kind C, enclosing_func is None): pass None — the
        #     class is `exported`-rooted and `_seed_exported_class_methods` rescues its PUBLIC
        #     methods, keeping a private method dead-eligible (R46A precision).
        #   * class member NESTED IN A FUNCTION (kind C, enclosing_func not None): the class is
        #     NOT exported-rooted (it's reachability-gated to the enclosing func via the CONTAINS
        #     edge above), so its methods must be gated to the CLASS (cid) — else they are
        #     orphaned (no role, no containment) and confidently flagged dead while live (the
        #     cardinal sin; panel round 3). Chain: enclosing func -> class -> methods.
        _collect(val, src, rel, spec, lang, qual, nodes, defs, inherits,
                 False, is_test, contains=contains,
                 enclosing_func=(cid if (kind is M or enclosing_func is not None) else None))


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


_EXPORT_SYMBOL_RE = re.compile(rb"\bEXPORT_SYMBOL\w*\s*\(\s*([A-Za-z_]\w*)")


def _export_symbol_names(src: bytes) -> set[str]:
    """Function names a Linux/driver `EXPORT_SYMBOL(foo)` / `EXPORT_SYMBOL_GPL(foo)` /
    `EXPORT_SYMBOL_NS(foo, ns)` macro marks as public kernel/module ABI — invoked by code
    outside this tree, so never dead for lack of an in-tree caller (the C analogue of
    `__all__` / `module.exports`). Text-scanned: the macro doesn't parse as a call expression
    in the grammar, and a byte regex is robust to the surrounding declaration context."""
    return {m.decode("ascii", "ignore") for m in _EXPORT_SYMBOL_RE.findall(src)}


def _reexport_names(root, src):
    """Local symbol names an `export`/`module.exports` form marks as public API: the
    `name` field of each `export { A, B as C }` specifier; the identifier of
    `export default Foo;` (the React/Angular/Vue/Node idiom — define a class/fn, then
    default-export it); and the whole-module CJS/TS-interop forms `module.exports = Foo`
    / `module.exports = { A, B }` / `exports.x = Foo` / `export = Foo`. Without these the
    public-exported symbol is false-flagged dead (Panel FF + GG)."""
    names: set[str] = set()

    def add_value(n):  # collect the named symbol(s) on the RHS of a default/CJS export
        if n.type in ("identifier", "type_identifier"):
            names.add(_text(n, src))
        elif n.type == "object":  # `module.exports = { A, B: localB }`
            for c in n.children:
                if c.type == "shorthand_property_identifier":
                    names.add(_text(c, src))
                elif c.type == "pair":
                    v = c.child_by_field_name("value")
                    if v is not None and v.type in ("identifier", "type_identifier"):
                        names.add(_text(v, src))

    def rec(n):
        if n.type == "export_specifier":
            nm = n.child_by_field_name("name")
            t = _trailing_id(nm, src) if nm is not None else None
            if t:
                names.add(t)
        elif n.type == "export_statement" and any(c.type in ("default", "=") for c in n.children):
            # `export default Foo;` / TS `export = Foo;` — a bare identifier child names a
            # predefined symbol. Inline `export default class/function …` has a declaration
            # child (the export_statement -> exported recursion marks it) and anonymous
            # `export default () => {}` / `{…}` has no identifier child, so both are skipped.
            for c in n.children:
                add_value(c)
        elif n.type == "assignment_expression":
            left = n.child_by_field_name("left")
            if left is not None and left.type in ("member_expression", "subscript_expression"):
                lt = _text(left, src)
                if (lt == "module.exports" or lt.startswith("module.exports.")
                        or lt.startswith("exports.")):  # CommonJS export targets
                    right = n.child_by_field_name("right")
                    if right is not None:
                        add_value(right)
        for c in n.children:
            rec(c)

    rec(root)
    return names


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


_is_test_file = is_test_file  # shared with the Python extractor (see ._testfile)


def _is_test_name(name: str) -> bool:
    """A test *entry* by conventional naming (Go Test*/Benchmark*/Example*,
    pytest/JS test*). Other helpers in a test file are live only if a test
    reaches them — so they aren't blanket-marked."""
    return name[:4].lower() == "test" or name.startswith(("Benchmark", "Example"))


# -- pass 2: calls ----------------------------------------------------------
def _direct_calls(body, src, spec):
    # (name, line, is_method): is_method marks a receiver-based call (`obj.save()`),
    # whose target type is unknown without type inference — see _callee / issue #10.
    out: list[tuple[str, int, bool]] = []

    def rec(n, top):
        for c in n.children:
            if not top and (c.type in spec.defs or c.type in spec.container_only):
                continue
            if c.type in spec.call_types:
                name, is_method = _callee(c, src, spec.call_types[c.type])
                if name:
                    out.append((name, c.start_point[0] + 1, is_method))
            elif spec.bare_calls and c.type == "identifier" and _is_bare_call(n, c):
                # A paren-less receiver-less Ruby call is a direct call, not a method
                # on a receiver — confident (not is_method).
                out.append((_text(c, src), c.start_point[0] + 1, False))
            rec(c, False)

    rec(body, True)
    return out


def _module_uses(root, src, spec):
    """Calls AND name-references made at *module scope* — descending into anonymous
    callbacks (the arrow/function/block bodies of `test()`/`it()`/`describe()`) but NOT
    into named defs (scanned per-def) or import statements. Used only for test files, to
    root the `test()`->helper chain in call-based suites (Jest/Mocha/Vitest, RSpec) which
    define no named test functions (Bug B). The refs half mirrors `_direct_refs`, so a
    class used by name as a call receiver (`Service.run` in an RSpec block) is rooted too
    and isn't flagged dead while its method is live (Panel FF). A symbol used by nothing
    still gets no edge and stays flagged. Returns (calls, refs)."""
    calls: list[tuple[str, int]] = []
    refs: list[tuple[str, int]] = []

    def rec(n):
        for c in n.children:
            if c.type in spec.defs or c.type in spec.container_only or c.type in spec.imports:
                continue  # a named def/class/import — handled elsewhere or not a use
            # A `const helper = () => {…}` / `= function(){…}` is itself a def (arrow_decls,
            # scanned per-def); don't descend, else its body's uses are double-counted and
            # over-rooted from the module even when the helper is uncalled (Panel GG).
            if spec.arrow_decls and c.type == "variable_declarator":
                val = c.child_by_field_name("value")
                if val is not None and val.type in (
                        "arrow_function", "function", "function_expression",
                        "generator_function"):
                    continue
            if c.type in spec.call_types:
                nm, _ = _callee(c, src, spec.call_types[c.type])
                if nm:
                    calls.append((nm, c.start_point[0] + 1))
            elif spec.bare_calls and c.type == "identifier" and _is_bare_call(n, c):
                calls.append((_text(c, src), c.start_point[0] + 1))
            if c.type in ("identifier", "type_identifier", "constant", "name"):
                refs.append((_text(c, src), c.start_point[0] + 1))
            elif spec.attr_suffix and c.type == "attribute":
                # Same C# `[Foo]` -> `FooAttribute` suffix as _direct_refs (R64), but for
                # attributes on declarations NOT in `spec.defs` — `enum`/`delegate` — whose
                # bodies _module_uses walks instead of _direct_refs (panel R66, sonnet).
                suffixed = _csharp_attribute_suffix_ref(c, src)
                if suffixed is not None:
                    refs.append((suffixed, c.start_point[0] + 1))
            rec(c)

    rec(root)
    return calls, refs


_RUBY_SYMBOL_DISPATCH = frozenset({"enum_for", "to_enum", "method", "instance_method"})
# A Ruby method name: an identifier with an optional `?`/`!`/`=` suffix (`valid?`, `save!`,
# `name=`). Operator-method symbols (`:+`, `:[]`) are not matched — rare and not the target here.
_RUBY_METHOD_NAME_RE = re.compile(r"[A-Za-z_]\w*[?!=]?$")


def _ruby_symbol_name(node, src):
    """`:upcase` -> "upcase", `:valid?` -> "valid?", `:name=` -> "name"; None for a non-method-name
    or dynamic symbol. A setter def (`def name=`) is keyed WITHOUT the trailing `=` (the def-name
    extractor strips the `=` operator), while `?`/`!` are part of the name and kept — so drop a
    trailing `=` here too, or `method(:name=)` would emit `name=` and match no def (the live setter
    would stay flagged dead)."""
    t = _text(node, src)
    if t.startswith(":"):
        name = t[1:]
        if _RUBY_METHOD_NAME_RE.fullmatch(name):
            return name[:-1] if name.endswith("=") else name
    return None


def _ruby_symbol_refs(root, src):
    """Ruby idioms that name a method via a literal SYMBOL the call graph otherwise can't see, so
    the method (and its callees) is false-flagged dead (Ruby dogfood, cardinal):
      * `xs.map(&:upcase)` — `Symbol#to_proc` turns `:upcase` into a block that calls `upcase`.
      * `enum_for(:m, …)` / `to_enum(:m)` — wrap method `m` as a lazy enumerator (invoked later).
      * `method(:m)` / `instance_method(:m)` — a (bound/unbound) Method for `m`, commonly invoked
        as `&method(:m)`.
    Yield (name, line) for each literal symbol so `_ref` roots it iff it names a project method
    (cardinal-safe; over-rooting a same-named method is the precision-over-recall direction). NOT
    `send`/`public_send` — those remain the documented dynamic-dispatch limitation."""
    out: list[tuple[str, int]] = []

    def rec(n):
        for c in n.children:
            if c.type == "block_argument":
                s = next((k for k in c.children if k.type == "simple_symbol"), None)
                if s is not None:
                    name = _ruby_symbol_name(s, src)
                    if name:
                        out.append((name, s.start_point[0] + 1))
            elif c.type == "call" and _field_text(c, "method", src) in _RUBY_SYMBOL_DISPATCH:
                al = c.child_by_field_name("arguments")
                s = next((k for k in al.children if k.type == "simple_symbol"), None) if al else None
                if s is not None:
                    name = _ruby_symbol_name(s, src)
                    if name:
                        out.append((name, s.start_point[0] + 1))
            rec(c)

    rec(root)
    return out


def _bash_callback_refs(root, src):
    """Bash commands that invoke a project function whose name sits in an ARGUMENT position,
    not the command head — so the generic command scan (which keys on the head) misses it.
    Yield each such function name as (name, line) so `_ref` roots it IFF it resolves to a
    project function (cardinal-safe: a non-matching name roots nothing; over-rooting a
    shell-invoked entry point is the documented precision-over-recall direction):
      * `trap HANDLER SIGNAL…` — HANDLER runs on the signal (issue #22). Only the handler
        (first non-option arg) is taken, never a trailing signal word.
      * `complete -F FUNC cmd` / `compgen -F FUNC …` — FUNC is the completion callback the
        shell invokes on TAB.
      * `export -f FUNC…` — each FUNC is exported for subshells (a strong invoked-elsewhere
        signal; `bash -c 'FUNC'` in a child shell is otherwise invisible).
      * `time FUNC` — the `time` keyword runs FUNC, but tree-sitter parses `time` as the
        command and FUNC as a plain word, so the call is otherwise lost.
    Descends function bodies too (unlike the old trap-only top-level pass): a trap/complete
    handler registered inside a function is still shell-invoked, so rooting it regardless of
    the enclosing function's own liveness is correct."""
    out: list[tuple[str, int]] = []

    def rec(n):
        for c in n.children:
            if c.type == "command":
                cn = next((k for k in c.children if k.type == "command_name"), None)
                head = _text(cn.children[0] if cn and cn.children else cn, src) if cn else ""
                if head == "trap":
                    _trap_handler(c, cn, src, out)
                elif head in ("complete", "compgen"):
                    _bash_flag_arg(c, cn, src, out, "-F")
                elif head == "time":
                    _bash_time_target(c, cn, src, out)
            elif c.type == "declaration_command":
                _bash_export_decl(c, src, out)   # `export -f FUNC` (parses as a declaration)
            rec(c)

    rec(root)
    return out


def _bash_command_words(call, cn, src):
    """The `word`/string children of a bash `command`, in order, paired with their 1-based line —
    skipping the command name itself. A quoted argument (`string`/`raw_string`) yields its
    quote-stripped text so a quoted bare identifier (`time "bench"`) is recognised, matching
    `_trap_handler`. The unit the callback-arg parsers below scan."""
    for arg in call.children:
        if arg is cn:
            continue
        if arg.type == "word":
            yield _text(arg, src), arg.start_point[0] + 1
        elif arg.type in ("string", "raw_string"):
            yield _text(arg, src).strip().strip("\"'`"), arg.start_point[0] + 1


def _bash_flag_arg(call, cn, src, out, flag):
    """Root the function named in the slot DIRECTLY after each `flag` occurrence (e.g.
    `complete -F FUNC`). The immediate next argument is the completion handler regardless of its
    node type — so a dynamic form (`complete -F ${VAR} cmd`, `-F $(f) cmd`) consumes the slot and
    roots nothing rather than falling through to a later word (the command being completed). A
    quoted bare identifier (`-F "_comp"`) is unwrapped; only a static identifier is rooted.

    Bash uses the LAST `-F` when several appear on one command, so every `-F` slot is rooted —
    over-rooting an overwritten handler is cardinal-safe and guarantees the effective one stays
    live (rooting only the first would flag the live last handler dead)."""
    args = [c for c in call.children if c is not cn]
    for i, c in enumerate(args):
        if c.type == "word" and _text(c, src) == flag and i + 1 < len(args):
            nxt = args[i + 1]
            if nxt.type in ("word", "string", "raw_string"):
                name = _text(nxt, src).strip().strip("\"'`")
                if name.isidentifier():
                    out.append((name, nxt.start_point[0] + 1))


def _bash_export_decl(call, src, out):
    """`export -f FUNC…` exports each named function for subshells (invoked via `bash -c
    'FUNC'` in a child shell — otherwise invisible). It parses as a `declaration_command`
    (`export`, word `-f`, then `variable_name` FUNC), so root each FUNC once `-f` is seen.
    A plain `export VAR=…` (no `-f`) roots nothing."""
    if not call.children or _text(call.children[0], src) != "export":
        return
    seen_f = False
    for ch in call.children[1:]:
        t = _text(ch, src)
        if not seen_f:
            if ch.type == "word" and t == "-f":
                seen_f = True
            continue
        if ch.type in ("variable_name", "word") and t.isidentifier():
            out.append((t, ch.start_point[0] + 1))


def _bash_time_target(call, cn, src, out):
    """`time FUNC` runs FUNC under the `time` keyword. Root the first non-option word when it
    is a bare identifier (a project function); `time -p FUNC` skips the `-p` option."""
    for w, line in _bash_command_words(call, cn, src):
        if w.startswith("-"):
            continue
        if w.isidentifier():
            out.append((w, line))
        return


def _trap_handler(call, cn, src, out):
    """Parse `trap` per its grammar `trap [-lp] [[--] ARG] SIGNAL…` and root ONLY the
    handler ARG when it's statically a project-function name — never a trailing signal
    word (panels XX/YY/ZZ). `-l` (list signals) and `-p` (print traps) are query modes
    with NO handler at all; `--` ends options; `-` resets the trap (no handler)."""
    opts_done = False
    for arg in call.children:
        if arg is cn:
            continue
        if arg.type == "word":
            w = _text(arg, src)
            if not opts_done:
                if w in ("-l", "-p"):
                    return  # query/list mode: no handler, remaining words are signals
                if w == "--":
                    opts_done = True
                    continue
            # First non-option word is the handler ARG. `-` resets the trap (no handler);
            # any other bare word is the handler; remaining words are signals.
            if w != "-":
                out.append((w, arg.start_point[0] + 1))
            return
        if arg.type in ("string", "raw_string"):
            # A quoted handler: root it only if it's a single bare identifier (a quoted
            # function name); an inline command string / empty string roots nothing.
            # Either way the handler slot is consumed — don't fall through to the signal.
            text = _text(arg, src).strip().strip("\"'`")
            if text.isidentifier():
                out.append((text, arg.start_point[0] + 1))
            return
        # Any other handler-slot shape ($(...) substitution, $var expansion,
        # concatenation) is dynamic — not statically resolvable; consume the slot.
        return


def _is_bare_call(parent, ident):
    """Ruby: a paren-less, receiver-less method call (`validate`) parses as a bare
    `identifier`, indistinguishable from a local-variable read. Treat one as a call
    unless it is structurally *not* a call — a def/class name, a parameter, an
    assignment target, or the method/receiver inside an enclosing `call` (already
    handled by `call_types`). Anything else over-approximates through `_ref`, which
    links only to project-defined methods and drops unknowns — the precision-safe
    direction (it can over-count reachability, never under-count)."""
    pt = parent.type
    if pt in ("method", "singleton_method", "class", "module", "call",
              "method_parameters", "block_parameters", "lambda_parameters",
              "keyword_parameter", "optional_parameter"):
        return False
    if pt in ("assignment", "operator_assignment"):
        left = parent.child_by_field_name("left")
        if left is not None and (left.start_byte, left.end_byte) == (
                ident.start_byte, ident.end_byte):
            return False
    return True


def _php_callable_names(node, src):
    """The method named by a PHP 2-element array callable
    `[$this|self::class|static::class|'Class'|$obj, 'method']` — the form PHP resolves at
    runtime (usort/uasort/preg_replace_callback/array_map comparators) but a syntactic call
    scan misses, so the live target is false-flagged dead (panel R53, the Magento idiom).
    Emit the method name so it isn't flagged dead; over-approximated through `_ref` (only
    project symbols resolve), so a non-callable 2-element array merely over-roots —
    cardinal-safe, never a false-dead. A `'Class::method'` *string* callable needs no handling:
    a string static call requires a PUBLIC target, which is already rooted as exported."""
    if node.type != "array_creation_expression":
        return []
    elems = [c for c in node.children if c.type == "array_element_initializer"]
    if len(elems) != 2:
        return []
    sval = next((c for c in elems[1].children
                 if c.type in ("string", "encapsed_string")), None)
    if sval is None:
        return []
    meth = _text(sval, src).strip().strip("\"'`").strip()
    return [(meth, sval.start_point[0] + 1)] if meth else []


# PHP builtins that take a callback by string name. A bare-string callable passed to one of these
# names a project global function the syntactic call scan can't see; scoping to this set keeps an
# ordinary string literal that merely matches a function name from over-rooting (panel R86).
_PHP_CALLBACK_BUILTINS = frozenset({
    "usort", "uasort", "uksort", "call_user_func", "call_user_func_array",
    "array_map", "array_filter", "array_walk", "array_walk_recursive", "array_reduce",
    "preg_replace_callback", "preg_replace_callback_array", "array_udiff", "array_uintersect",
    "register_shutdown_function", "set_error_handler", "set_exception_handler",
    "spl_autoload_register", "forward_static_call", "iterator_apply",
})


def _php_string_callable_names(call_node, src):
    """Project function names passed as a bare-STRING callback to a known PHP callback builtin —
    `usort($x, 'topcmp')`, `call_user_func('handler')`. The syntactic call scan can't see the
    string, so the live target is false-flagged dead (panel R86, the bare-string analogue of the
    v2.0.1 array form). Scoped to `_PHP_CALLBACK_BUILTINS` so an ordinary string literal matching a
    function name doesn't over-root. Over-approximated through `_ref` (only project symbols resolve);
    a `'Class::method'` string needs no handling (a static string call requires a public target,
    already rooted)."""
    if call_node.type != "function_call_expression":
        return []
    callee = call_node.child_by_field_name("function")  # PHP always exposes the callee here
    if callee is None or _text(callee, src).strip().lstrip("\\") not in _PHP_CALLBACK_BUILTINS:
        return []
    args = next((c for c in call_node.children if c.type == "arguments"), None)
    if args is None:
        return []
    out: list[tuple[str, int]] = []
    for arg in args.children:
        if arg.type != "argument":
            continue
        for s in arg.children:
            if s.type in ("string", "encapsed_string"):
                nm = _text(s, src).strip().strip("\"'`").strip()
                if nm and "::" not in nm and "\\" not in nm:
                    out.append((nm, s.start_point[0] + 1))
    return out


def _csharp_attribute_suffix_ref(attr_node, src):
    """The `<Name>Attribute` form of a C# `[Name]` attribute usage, or None. C# omits the
    `Attribute` suffix when applying an attribute, so `[NoEnumeration]` references class
    `NoEnumerationAttribute`; the bare name never resolves on its own (panel R64). Returns None
    when the name can't be read or already ends in `Attribute` (the bare name already resolves).
    `_trailing_id` reduces a qualified attribute (`[My.Ns.Foo]`) to its trailing name (`Foo`)."""
    an = attr_node.child_by_field_name("name")  # reliable for C# attribute (identifier/qualified)
    if an is None:
        return None
    nm = _trailing_id(an, src)
    if not nm or nm.endswith("Attribute"):
        return None
    return nm + "Attribute"


def _direct_refs(body, src, spec):
    """Identifier/type references in a def body (not crossing nested defs, excluding
    the def's own name): a symbol used by name as a value or type. Emitted as
    REFERENCES so a symbol used only by name — passed as a callback, a class as a
    `new`/`.new` receiver, a type annotation — isn't false-flagged dead. Over-
    approximated through `_ref` (only project symbols resolve)."""
    out: list[tuple[str, int]] = []
    name_node = body.child_by_field_name("name")
    # Compare by byte span, not id(): the tree-sitter bindings hand back a fresh
    # wrapper object on every access, so `id()` of the same node never matches.
    name_span = (name_node.start_byte, name_node.end_byte) if name_node is not None else None

    def rec(n, top):
        for c in n.children:
            if not top and (c.type in spec.defs or c.type in spec.container_only):
                continue
            if c.type in ("identifier", "type_identifier", "constant", "name") \
                    and (c.start_byte, c.end_byte) != name_span:
                out.append((_text(c, src), c.start_point[0] + 1))
            elif c.type == "selector_expression":
                # Go method VALUE / method EXPRESSION / package-qualified reference:
                # `v.run` (bound method value passed as a callback), `T.run` (unbound method
                # expression), `cfg{onRun: v.run}` (struct-field value). The method is
                # REFERENCED, not called, so `_direct_calls` never sees it and an unexported
                # method reached only as a callback value was confidently flagged dead (#49,
                # cobra dogfood — `selector_expression` is unique to the Go grammar). Emit the
                # trailing field name as a by-name reference (resolves only to a project
                # symbol; a plain struct-field access that happens to share a name with a
                # function is cardinal-safe over-rooting). A `v.run()` CALL also contains this
                # selector, but the edge loop dedups REFERENCES against the CALLS set, so it
                # never double-counts.
                fld = c.child_by_field_name("field")
                if fld is not None:
                    out.append((_text(fld, src), fld.start_point[0] + 1))
            elif spec.callable_strings:
                # PHP array callables (`[$this, 'm']`) name a method the syntactic call scan
                # can't see; emit it so the live target isn't flagged dead. Bare-string callables
                # passed to a known callback builtin (`usort($x, 'topcmp')`) name a global function
                # the scan also misses (panel R86).
                out.extend(_php_callable_names(c, src))
                out.extend(_php_string_callable_names(c, src))
            elif spec.attr_suffix and c.type == "attribute":
                # C# applies an attribute with the `Attribute` suffix OMITTED — `[NoEnumeration]`
                # names class `NoEnumerationAttribute`. The generic walk already emits the bare
                # `NoEnumeration` (which won't resolve); also emit the suffixed form so the
                # in-tree attribute class isn't false-flagged dead (panel R64, serilog dogfood).
                suffixed = _csharp_attribute_suffix_ref(c, src)
                if suffixed is not None:
                    out.append((suffixed, c.start_point[0] + 1))
            rec(c, False)

    rec(body, True)
    return out


# Call *node* types that are themselves receiver-based (the receiver isn't reachable
# via the callee node, only via the call node's shape): PHP `$o->m()` / `C::m()`.
_RECEIVER_CALL_NODES = frozenset({"member_call_expression", "scoped_call_expression"})
# Call-fields that name a *constructor* (a type, directly) rather than a method on a
# receiver: JS/cpp `new …` -> "constructor"/"type", C#/Java `object_creation_expression`
# -> "type". A constructor is never a receiver call even when the type is namespace-
# qualified (C# `new MyApp.Widget()` whose callee node is a `qualified_name`), so these
# must stay EXTRACTED, not be demoted by #10 (issue found in panel KK by sonnet).
_CONSTRUCTOR_FIELDS = frozenset({"constructor", "type"})
# Callee *node* types that access a member/field on a receiver, so the call names a
# method on a value of unknown type: `obj.save()` (py attribute / js member_expression /
# rust field_expression / go selector_expression / c# member_access_expression),
# `Foo::bar()` (rust scoped_identifier), qualified names.
_RECEIVER_CALLEE = frozenset({
    "attribute", "member_expression", "field_expression", "selector_expression",
    "member_access_expression", "scoped_identifier", "qualified_name",
})


def _callee(call, src, field):
    """Return (name, is_method). `is_method` is True for a receiver-based call —
    `obj.save()`, `Class::save()`, `x->save()` — whose target type we can't know
    without type inference, so a lone same-named project symbol is recorded as a
    guess (INFERRED) rather than asserted (EXTRACTED). See issue #10. Weight is
    unchanged, so the edge still counts for reachability — this only labels
    confidence, it never drops the edge (cardinal-safe)."""
    if field is None:  # bash: command -> command_name
        cn = next((c for c in call.children if c.type == "command_name"), None)
        if cn is not None:
            return _text(cn.children[0] if cn.children else cn, src), False
        return None, False
    fn = call.child_by_field_name(field)
    if fn is None:
        return None, False
    name = _trailing_id(fn, src)
    # A constructor names a type directly — no receiver ambiguity — even when the type is
    # namespace-qualified; never demote it (keeps `new ns.Widget()` EXTRACTED).
    if field in _CONSTRUCTOR_FIELDS:
        return name, False
    # Detected three ways across grammars: the call node itself is a member/scoped call
    # (PHP), the callee node is a member/field/selector/scoped access (Python/JS/Go/C#/
    # Rust), or the call carries an explicit object/receiver field (Java/Ruby). A bare
    # `foo()` / a constructor (`new Foo()`, naming a type directly) has none of these.
    is_method = (
        call.type in _RECEIVER_CALL_NODES
        or fn.type in _RECEIVER_CALLEE
        or call.child_by_field_name("object") is not None    # java method_invocation
        or call.child_by_field_name("receiver") is not None  # ruby call
    )
    return name, is_method


def _trailing_id(node, src):
    if node is None:
        return None
    if node.type in ("identifier", "type_identifier", "field_identifier",
                     "property_identifier", "word", "name", "constant"):
        return _text(node, src)
    # C++ operator/destructor/conversion names (`operator+`, `~Class`, `operator bool`) are
    # leaf-ish multi-token nodes; take their literal text so an out-of-line def gets a real
    # node name instead of None. `operator_cast` (conversion ops) has a `type` field that the
    # generic walk below would follow to the target type (`bool`) and lose the name, so it
    # must be handled here too (panels R13A/R14A).
    # C++ `operator_name`/`destructor_name`/`operator_cast`; Ruby `operator` (the name node
    # of `def []`, `def []=`, `def <=>`, `def ==`, …). Without `operator`, every Ruby operator
    # method was dropped from the graph — so it was un-navigable AND anything used only inside
    # its body (e.g. `ValueArray.new(value)` inside `def []=`) was false-flagged dead (panel
    # R61, the grape dogfood: ValueArray's constructor flagged dead though it is instantiated).
    if node.type in ("operator_name", "destructor_name", "operator_cast", "operator"):
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


def _ref(edges, src_id, name, by_name, rel, line, relation=Relation.CALLS,
         is_method=False):
    cands = by_name.get(name, [])
    # A function may legitimately CALL itself (recursion) — keep the self-edge, as
    # the Python extractor does, so both model the same graph. Self-reference is
    # meaningless for INHERITS/IMPORTS (a class/module can't inherit/import itself)
    # and for REFERENCES (a def naming itself carries no liveness/impact), so drop it
    # there — otherwise every def gets a spurious self-loop that inflates degree
    # metrics / get_matrix / pagerank.
    if relation in (Relation.INHERITS, Relation.IMPORTS, Relation.REFERENCES):
        cands = [c for c in cands if c != src_id]
    loc = f"{rel}:{line}:0"
    if not cands:
        return
    if len(cands) == 1:
        # A receiver-based call (`obj.save()`) that resolves to a *single* same-named
        # project symbol is still a guess without type inference — the receiver's type
        # is unknown, so this might be a homonym `save` on a different class (issue #10).
        # Record it as INFERRED, not EXTRACTED. Weight stays 1.0 so the edge still
        # counts fully for reachability/find_stale (never under-counts a live caller —
        # cardinal-safe); only the asserted confidence is lowered.
        prov = (Provenance.INFERRED if is_method and relation is Relation.CALLS
                else Provenance.EXTRACTED)
        edges.append(Edge(src=src_id, relation=relation, dst_symbol=name,
                          dst_id=cands[0], weight=1.0, provenance=prov,
                          location=loc, source="tree-sitter", name_based=True))
    else:
        w = round(1.0 / len(cands), 3)
        for cid in cands:
            edges.append(Edge(src=src_id, relation=relation, dst_symbol=name,
                              dst_id=cid, weight=w, provenance=Provenance.AMBIGUOUS,
                              location=loc, source="tree-sitter", name_based=True))


# -- helpers ---------------------------------------------------------------
def _is_ruby_operator_method(name: str) -> bool:
    """True if `name` is a Ruby operator method name (`[]`, `[]=`, `<=>`, `==`, `<<`, `+`, …) —
    i.e. it does not start with a letter or underscore. Such methods are invoked via operator/
    index syntax, not a by-name call, so they're rooted (panel R61). A normal method, predicate
    (`valid?`) or bang (`save!`) starts with a letter and is excluded (it IS called by name)."""
    return bool(name) and not (name[0].isalpha() or name[0] == "_")


def _name_of(node, src):
    nm = node.child_by_field_name("name")
    if nm is not None:
        return _trailing_id(nm, src)
    # C/C++: the name is in the `declarator`, not the `type` field (which is the
    # *return type*). This must run before the `type` fallback, or a function like
    # `int helper(...)` resolves to its return type (yielding None -> the whole def
    # was silently dropped) and `Widget* create()` resolves to `Widget`.
    decl = node.child_by_field_name("declarator")  # C / C++
    while decl is not None:
        if decl.type in ("identifier", "field_identifier"):
            return _text(decl, src)
        # An out-of-line member def names the function with a `qualified_identifier`
        # (`Class::m`, `Outer::Inner::m`) or an `operator_name`/`destructor_name`
        # (`Class::operator+`, `Class::~Class`); the trailing name is nested, so use
        # _trailing_id. Without this _name_of returned None and the WHOLE
        # function_definition was silently dropped, so a helper called only from a
        # nested-class or operator body was flagged dead (panel R13A, cardinal).
        if decl.type in ("qualified_identifier", "operator_name", "destructor_name",
                         "operator_cast"):
            return _trailing_id(decl, src)
        nxt = decl.child_by_field_name("declarator")
        if nxt is None:
            ident = next((c for c in decl.children
                          if c.type in ("identifier", "field_identifier",
                                        "qualified_identifier", "operator_name",
                                        "destructor_name", "operator_cast")), None)
            if ident is not None:
                return _trailing_id(ident, src)
            # `reference_declarator` (`T&`/`T&&`) exposes its inner function_declarator as an
            # UNNAMED child (no `declarator` field), unlike pointer_declarator — descend into
            # any declarator-wrapper child so a reference-returning fn/method isn't dropped
            # (panel R15A, cardinal). Robust to the whole declarator-wrapper family.
            nxt = next((c for c in decl.children if c.type in _DECLARATOR_WRAPPERS), None)
            if nxt is None:
                return None
        decl = nxt
    # Rust `impl Container { ... }` names its target via the `type` field.
    ty = node.child_by_field_name("type")
    if ty is not None:
        got = _trailing_id(ty, src)
        if got:
            return got
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "property_identifier"):
            return _text(c, src)
    return None


def _is_cpp_special_member(name: str) -> bool:
    """A C++ operator overload (`operator+`, `operator[]`, `operator bool`) or destructor
    (`~Class`) — invoked implicitly, so the name-based call graph can't see the use. Used to
    root them so live code reached only through one isn't flagged dead (panel R13A). `operator`
    is a reserved word, so the prefix can't be an ordinary identifier."""
    if name.startswith("~"):
        return True
    return (name.startswith("operator") and len(name) > 8
            and not (name[8].isalnum() or name[8] == "_"))


def _cpp_method_scope(node, src):
    """For a C/C++ out-of-line member definition `RetT Scope::method(...)`, return the
    enclosing class/struct name `Scope` so the method can be linked back to its class
    (panel R12B). A free function (`int helper(...)`) has a plain identifier declarator
    and yields None. The class name lives in the `scope` field of the `qualified_identifier`
    that names the function inside the declarator chain (`pointer_declarator` for `T*`)."""
    def _scope_of(qi):
        # `scope` is a `namespace_identifier` leaf for `Class::m` (which _trailing_id
        # doesn't recognise), or a nested `qualified_identifier` for `A::B::m` (take the
        # immediate enclosing name). Fall back to the raw text for the leaf case.
        scope = qi.child_by_field_name("scope")
        if scope is None:
            return None
        return _trailing_id(scope, src) or _text(scope, src) or None

    decl = node.child_by_field_name("declarator")
    while decl is not None:
        if decl.type == "qualified_identifier":
            return _scope_of(decl)
        nxt = decl.child_by_field_name("declarator")
        if nxt is None:
            qi = next((c for c in decl.children if c.type == "qualified_identifier"), None)
            if qi is not None:
                return _scope_of(qi)
            # descend through an unnamed declarator wrapper (`reference_declarator` for a
            # reference-returning out-of-line method) so its class link is still found (R15A)
            nxt = next((c for c in decl.children if c.type in _DECLARATOR_WRAPPERS), None)
            if nxt is None:
                return None
        decl = nxt
    return None


# C/C++ declarator wrappers that nest an inner declarator; some (reference_declarator)
# expose it as an unnamed child rather than a `declarator` field, so the name/scope walks
# must descend into them explicitly or a def is silently dropped (panel R15A).
_DECLARATOR_WRAPPERS = ("function_declarator", "pointer_declarator", "reference_declarator",
                        "array_declarator", "parenthesized_declarator", "init_declarator")


_GO_EXPORT_RE = re.compile(r"^//\s*export\s+(\w+)\s*$")


def _go_has_export_directive(node, name: str, src) -> bool:
    """True if the Go func `node` is immediately preceded by a `//export <name>` cgo comment
    naming it. cgo requires the directive on the line directly above the func, which the
    tree-sitter grammar exposes as the func's previous `comment` sibling."""
    prev = node.prev_sibling
    if prev is None or prev.type != "comment":
        return False
    m = _GO_EXPORT_RE.match(_text(prev, src).strip())
    return m is not None and m.group(1) == name


def _roles(node, src, name, lang, exported):
    roles = set()
    if exported:
        roles.add("exported")
    if name in ("main", "Main"):
        roles.add("main")
    # Go `init()` is a runtime entry point: the Go runtime calls it automatically at package
    # initialization (driver/handler registration, etc.), never from source — so it (and its
    # callees) must be rooted like main, or it's flagged dead (panel R18A, cardinal). Go-gated
    # so a plain `init` in another language isn't spuriously rooted; cardinal-safe for a (rare)
    # method named init too.
    if lang == "go" and name == "init":
        roles.add("main")
    # Go cgo `//export Name` directly above a func makes it callable from C — a native entry point
    # with no in-tree caller. A *capitalised* one is already `exported` by the rule below, but a
    # lowercase `//export name` would be flagged dead (panel R88). The directive is the func's
    # immediately-preceding `comment` sibling. Go-gated; cardinal-safe (only adds a root).
    if lang == "go" and name and _go_has_export_directive(node, name, src):
        roles.add("exported")
    if lang == "rust" and any(c.type == "visibility_modifier" for c in node.children):
        roles.add("exported")
    elif lang == "go" and name[:1].isupper():        # Go: capitalised = exported
        roles.add("exported")
    elif lang in ("java", "php", "csharp") and _has_public(node, src):
        roles.add("exported")
    # A Java `native` method is a JNI entry point: it has no Java body and is implemented in C,
    # invoked across the JNI boundary — no in-tree by-name caller. A non-public one would be
    # false-flagged dead (manual pass; the Java analogue of Go cgo `//export` / C#
    # `[UnmanagedCallersOnly]` rooted in v2.1.9). Cardinal-safe: only adds a root.
    if lang == "java" and _is_java_native(node, src):
        roles.add("callback")
    if lang in ("c", "cpp"):
        roles |= _c_attr_roots(node, src)
    return frozenset(roles)


# Function-attribute node types across the C and C++ grammars: the GNU `__attribute__((…))`
# (`attribute_specifier`), the C++11 `[[…]]` form (`attribute_declaration`), and the MSVC
# `__declspec(…)` modifier (`ms_declspec_modifier`).
_C_ATTR_NODES = frozenset({"attribute_specifier", "attribute_declaration", "ms_declspec_modifier"})


def _c_dangling_attr_texts(node, src) -> list[bytes]:
    """Recover an attribute the tree-sitter C++ grammar mis-attached to `node`'s previous sibling.
    An *empty-body* inline method `void f() {}` is parsed as a `field_declaration` whose body is an
    `initializer_list`, and it ABSORBS the FOLLOWING declaration's leading attribute as a trailing
    `attribute_specifier` (after its own declarator). So `__attribute__((visibility("default")))
    void g() {}` right after an empty-body method loses its attribute and `g` is false-flagged dead
    (panel R75). Reattach any attribute in the prior `field_declaration` that sits AFTER its
    declarator — the field's own (leading) attribute is before the declarator, so this can't steal
    it. Cardinal-safe: at worst it copies an attribute onto an extra node (over-roots)."""
    prev = node.prev_sibling
    if prev is None or prev.type != "field_declaration":
        return []
    decl = next((c for c in prev.children if c.type == "function_declarator"), None)
    if decl is None:
        return []
    return [src[c.start_byte:c.end_byte] for c in prev.children
            if c.type in _C_ATTR_NODES and c.start_byte >= decl.end_byte]


def _c_attr_roots(node, src) -> set[str]:
    """Roots for a C/C++ function carrying a GCC/Clang/MSVC attribute that makes it an implicit
    entry point or an exported symbol — there is no in-tree by-name caller, so without rooting it
    (and everything its body reaches) is false-flagged dead (doc-driven, panel R73):
      * `constructor` / `destructor` — run automatically before/after `main` by the C runtime
        (the C analogue of a static initializer or Go `init`); the function definitely executes.
        Rooted `callback`.
      * `used` / `retain` — explicitly tells the compiler the symbol IS used and must be kept;
        the use is one it can't see by name (inline asm, a linker section). Rooted `callback`.
      * `visibility("default")` / `dllexport` — the explicit public-ABI surface (the analogue of
        Rust `#[no_mangle]` / a `pub` item). Rooted `exported`.
    Cardinal-safe: only ever ADDS roots, so a broad text match can over-root (mask dead code) but
    can never flag live code dead. Visibility is matched only for `"default"` — `"hidden"` is
    genuinely internal and must stay dead-code-eligible."""
    texts: list[bytes] = []
    for c in node.children:
        if c.type in _C_ATTR_NODES:
            texts.append(src[c.start_byte:c.end_byte])
        elif c.type == "function_declarator":   # the GNU *trailing* form attaches to the declarator
            texts.extend(src[g.start_byte:g.end_byte] for g in c.children if g.type in _C_ATTR_NODES)
    texts.extend(_c_dangling_attr_texts(node, src))
    if not texts:
        return set()
    blob = b" ".join(texts).decode("utf-8", "replace")
    roles: set[str] = set()
    # GCC accepts a `__name__` synonym for every attribute (`__constructor__`, `__used__`,
    # `__visibility__`, …) — common in system/library headers to dodge user macros — so allow an
    # optional `_*` around each keyword (panel R74). `_*` still can't over-match inside a longer
    # identifier like `my_constructor_helper`: both neighbours stay word chars, so no `\b` exists.
    #   * constructor/destructor — runtime-invoked around main; used/retain — explicitly kept;
    #     section — placed in a custom linker section (initcall tables, …), reached by the linker
    #     not by name; interrupt/interrupt_handler/signal/signal_handler — an ISR invoked by the
    #     hardware vector table (embedded C: ARM/MIPS/m68k `interrupt`/`interrupt_handler`, AVR
    #     `signal`), never by an in-tree call. The explicit `(?:_handler)?` is required because the
    #     `_*\b` synonym handling covers underscores AROUND a keyword (`__interrupt__`) but not a
    #     trailing word like `_handler` (after `interrupt`, the `_` is a word char so `\b` fails).
    #     All implicit entry points -> callback.
    if re.search(r"\b_*(?:constructor|destructor|used|retain|section"
                 r"|interrupt(?:_handler)?|signal(?:_handler)?)_*\b", blob):
        roles.add("callback")
    #   * visibility("default")/dllexport — public ABI; weak — a linker-visible (overridable)
    #     symbol callable from outside the tree. -> exported.
    if re.search(r"\b_*(?:dllexport|weak)_*\b", blob) \
            or re.search(r"\b_*visibility_*\s*\(\s*\"default\"", blob):
        roles.add("exported")
    return roles


# An `alias("target")` / `ifunc("resolver")` attribute names ANOTHER in-tree function that the
# linker/loader reaches through this symbol — `void old() __attribute__((alias("new")))` keeps
# `new` live; `__attribute__((ifunc("res")))` keeps the resolver `res` live. The attributed symbol
# is itself usually a body-less declaration (no node), but the named target has a real definition
# with no by-name caller and is otherwise flagged dead (panel R74). Text-scanned like EXPORT_SYMBOL
# — the name in the string literal isn't a call expression in the grammar.
_C_ALIAS_RE = re.compile(rb"_*(?:alias|ifunc)_*\s*\(\s*\"([A-Za-z_]\w*)\"")


def _c_alias_target_names(src: bytes) -> set[str]:
    """Function names kept live as the target of a C/C++ `alias(...)` / `ifunc(...)` attribute."""
    return {m.decode("ascii", "ignore") for m in _C_ALIAS_RE.findall(src)}


# An export attribute on a function/method *declaration* (`visibility("default")` / `dllexport`).
_C_EXPORT_ATTR_RE = re.compile(r"\b_*dllexport_*\b|\b_*visibility_*\s*\(\s*\"default\"")


def _c_export_decl_names(root, src: bytes) -> set[str]:
    """Simple names of C/C++ functions/methods *declared* (body-less) with an export attribute —
    `__attribute__((visibility("default"))) int Widget::compute(int);` in a header, or the same on
    an in-class member declaration. The export attribute commonly lives on the **header
    declaration** while the out-of-line definition in the `.cpp` carries none, so the definition has
    no in-tree caller and is false-flagged dead (panel R77 F2). Collected project-wide (declaration
    and definition live in different files) and used to root the matching definition by name — the
    C/C++ analogue of Python's project-wide `__all__`; cardinal-safe (over-roots a homonym only in
    the safe direction). Gated by a cheap byte test in the caller so the AST walk is skipped on the
    vast majority of files."""
    names: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in ("declaration", "field_declaration"):
            if any(c.type in _C_ATTR_NODES
                   and _C_EXPORT_ATTR_RE.search(src[c.start_byte:c.end_byte].decode("utf-8", "replace"))
                   for c in n.children):
                # Reuse the definition-side name extraction so the collected name MATCHES the
                # node id exactly. `_name_of` descends the declarator-wrapper chain (pointer/
                # reference/array-return), so a `char* W::make(int)` header export is collected as
                # `make` and roots its out-of-line def — the direct-child-only scan missed this and
                # left a pointer/reference-returning export false-flagged dead (panel R78, cardinal).
                # Guard on an actual function_declarator so a plain exported *variable* adds nothing.
                if _has_function_declarator(n):
                    nm = _name_of(n, src)
                    if nm:
                        names.add(nm)
        elif n.type in ("class_specifier", "struct_specifier"):
            # A C++ class/struct carrying a *class-level* export attribute — `class
            # __attribute__((visibility("default"))) Foo {…}` / `__declspec(dllexport)` — exports
            # its whole PUBLIC interface, so every public method is public ABI even with no
            # per-method attribute. Their out-of-line definitions carry no attribute and otherwise
            # false-flag dead at 0.6 (panel R80 F1, cardinal). Collect the public method names;
            # private members aren't ABI and stay dead-code-eligible. `struct` defaults to public,
            # `class` to private.
            if any(c.type in _C_ATTR_NODES
                   and _C_EXPORT_ATTR_RE.search(src[c.start_byte:c.end_byte].decode("utf-8", "replace"))
                   for c in n.children):
                names |= _c_public_method_names(n, src)
        stack.extend(n.children)
    return names


def _c_public_method_names(class_node, src: bytes) -> set[str]:
    """Public/protected method names in a C++ class/struct body (for a class-level export attr).
    Covers all three member shapes: a declared-only method (`field_declaration`), an INLINE-defined
    method (`function_definition` — body written in the class), and a templated method
    (`template_declaration`, descended into) — the last two parse differently and a field-only scan
    missed them, leaving inline/templated public methods false-flagged dead at 0.6 (panel R81,
    cardinal). `protected` is included: for an exported class it is reachable by out-of-tree
    subclasses (extensibility ABI). `private` is internal and excluded. `struct` defaults public,
    `class` private."""
    body = next((c for c in class_node.children if c.type == "field_declaration_list"), None)
    if body is None:
        return set()
    names: set[str] = set()
    exported = class_node.type == "struct_specifier"   # struct defaults public; class private
    for c in body.children:
        if c.type == "access_specifier":
            txt = src[c.start_byte:c.end_byte]
            exported = b"public" in txt or b"protected" in txt
            continue
        if not exported:
            continue
        member = c
        if c.type == "template_declaration":   # templated member: the fn is nested one level down
            member = next((g for g in c.children
                           if g.type in ("function_definition", "field_declaration", "declaration")),
                          None)
        if member is not None \
                and member.type in ("field_declaration", "function_definition", "declaration") \
                and _has_function_declarator(member):
            nm = _name_of(member, src)
            if nm:
                names.add(nm)
    return names


def _has_function_declarator(node) -> bool:
    """True if `node`'s declarator chain reaches a `function_declarator` — i.e. it declares a
    function/method, not a variable. Descends the pointer/reference/array-return wrappers."""
    decl = node.child_by_field_name("declarator")
    while decl is not None:
        if decl.type == "function_declarator":
            return True
        decl = decl.child_by_field_name("declarator") \
            or next((c for c in decl.children if c.type in _DECLARATOR_WRAPPERS), None)
    return False


def _has_public(node, src) -> bool:
    for c in node.children:
        if c.type in ("modifiers", "modifier", "visibility_modifier") \
                and "public" in _text(c, src):
            return True
        if c.type == "public":
            return True
    return False


def _is_java_native(node, src) -> bool:
    """A Java `native` method declaration (JNI entry point). The `native` keyword lives in the
    method's `modifiers` child; match it as a whitespace-delimited token so an annotation like
    `@Native`/`@NativeFoo` can't trigger it. Cardinal-safe: callers only add a root."""
    for c in node.children:
        if c.type in ("modifiers", "modifier") and "native" in _text(c, src).split():
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
    # Skip empty patterns: rel.match("") raises ValueError("empty pattern") (panel R33B).
    return not (ignore and any(rel.match(pat) for pat in ignore if pat))


def grammar_status() -> tuple[bool, list[tuple[str, bool, str]]]:
    """Probe every supported language's grammar: can it load right now? Powers the
    `doctor` self-check (issue #12 / #7's optional doctor idea). Returns
    `(all_ok, rows)` with `rows = [(language, loadable, detail)]`."""
    if not HAS_TREE_SITTER:
        return False, [("tree-sitter", False,
                        "not installed — pip install 'stitchgraph[treesitter]'")]
    langs = sorted(set(EXT_LANG.values()) & set(SPECS))
    rows: list[tuple[str, bool, str]] = []
    for lang in langs:
        try:
            Parser(_load_grammar(lang))
            rows.append((lang, True, "loaded"))
        except Exception as exc:  # noqa: BLE001
            rows.append((lang, False, f"{type(exc).__name__}: {str(exc)[:60]}"))
    return all(ok for _, ok, _ in rows), rows


def grammar_backend() -> dict:
    """Describe the installed tree-sitter-language-pack for the `doctor` self-check:
    version, whether it bundles grammars (offline) or downloads them, and the cache
    dir if it downloads."""
    if not HAS_TREE_SITTER:
        return {"installed": False}
    import importlib.metadata as _md

    import tree_sitter_language_pack as _pack
    can_download = hasattr(_pack, "download")
    info: dict = {"installed": True,
                  "version": _md.version("tree-sitter-language-pack"),
                  "model": "download-on-demand" if can_download else "bundled (offline)"}
    if can_download and hasattr(_pack, "cache_dir"):
        try:
            info["cache_dir"] = _pack.cache_dir()
        except Exception:  # noqa: BLE001
            pass
    return info
