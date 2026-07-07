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
    # INHERITS edges, retained separately: the post-edge passes (_apply_callback_roles,
    # _seed_test_classes, _seed_exported_inherited_methods) need ONLY this relation, and in
    # streaming mode `edges` is drained to the sink per file — this small tee (O(classes))
    # is what survives. Populated by extract_project in both modes so the passes read one
    # uniform source (the HA constant-memory fix, review 2026-07-03 follow-up).
    inherits: list[Edge] = field(default_factory=list)
    by_name: dict[str, list[str]] = field(default_factory=dict)
    class_by_name: dict[str, list[str]] = field(default_factory=dict)
    ids: set[str] = field(default_factory=set)
    packages: set[str] = field(default_factory=set)
    exported_names: set[str] = field(default_factory=set)
    main_calls: set[str] = field(default_factory=set)
    module_consts: set[str] = field(default_factory=set)  # module-level assigned names
    fixture_names: set[str] = field(default_factory=set)  # @pytest.fixture def names
    external_base_classes: set[str] = field(default_factory=set)  # subclass framework bases
    module_by_qual: dict[str, str] = field(default_factory=dict)  # module qualname -> node id
    module_ids: set[str] = field(default_factory=set)  # all MODULE node ids
    source_prefix: str = ""  # qualname prefix of a src-layout source root, e.g. "src." (else "")
    # Per-file record of the four raw pass-1 name-sets (rel -> kind -> names,
    # kinds: export/main/const/fixture). The global sets above are unions and
    # cannot say WHICH file contributed a name — but the store must (research/21:
    # `replace_file` maintains a per-file `symtab` table so single-file
    # re-extraction can rebuild each union with one file's contribution swapped).
    symtab: dict[str, dict[str, set[str]]] = field(default_factory=dict)


_SYM_KINDS = {"export": "exported_names", "main": "main_calls",
              "const": "module_consts", "fixture": "fixture_names"}


def _sym_add(proj: _Project, rel: str, kind: str, names) -> None:
    """Record pass-1 name-set entries BOTH globally (what pass 2 reads) and
    per-file (what the store persists). Single choke point so the two views
    can never drift."""
    names = [names] if isinstance(names, str) else list(names)
    if not names:
        return
    getattr(proj, _SYM_KINDS[kind]).update(names)
    proj.symtab.setdefault(rel, {}).setdefault(kind, set()).update(names)

# Ordinary bases whose subclasses are NOT framework callbacks — their methods
# should still be eligible for dead-code. Anything else external (HTMLParser,
# threading.Thread, a web View, …) is treated as a framework base.
_PLAIN_BASES = {
    "object", "Exception", "BaseException", "ValueError", "RuntimeError",
    "TypeError", "KeyError", "Protocol", "ABC", "ABCMeta", "Enum", "IntEnum",
    "Flag", "str", "int", "dict", "list", "tuple", "set", "frozenset",
    "NamedTuple", "TypedDict", "Generic", "Iterator", "Iterable",
}


# -- parallel extraction (v3.40.0) ------------------------------------------
# Parsing dominates index wall time and is single-core; a fork-based pool runs the
# per-file work of BOTH passes on all cores. Fork (Linux) is required: pass-2
# workers read the fully-built symbol table via copy-on-write, and results merge
# in sorted-file order, so output is byte-identical to the serial reference
# (pinned by tests/oracles). Serial remains the reference implementation and the
# automatic fallback (no fork, tiny trees, pure mode, or parallel=False).
_PARALLEL_MIN_FILES = 64
_WPROJ: _Project | None = None  # set pre-fork; inherited read-only by pass-2 workers


def _parallel_workers(n_files: int, parallel: bool | None, sink_mode: bool) -> int:
    if parallel is False:
        return 0
    import os as _os
    import sys as _sys

    from ..purity import pure_mode
    # Linux-only: pass-2 workers depend on fork's copy-on-write snapshot of the
    # symbol table, and fork on macOS is unsafe with threads. Pure mode forces
    # the serial reference path like every other accelerated twin.
    if _sys.platform != "linux" or pure_mode():
        return 0
    # AUTO stays OFF in sink (streaming) mode: measured on Django 5.2 (2,873
    # files, 3.7M edges), the streaming reindex is bounded by edge
    # materialisation + SQLite insertion — both parent-side — so the parse pool
    # only adds IPC overhead (181 s serial vs 190 s parallel end-to-end). The
    # in-memory path, where edges stay objects, wins 67 s -> 50 s. The true
    # index-time lever at scale is edge VOLUME (the homonym-compression arc),
    # not parse parallelism. `parallel=True` still forces the pool on for the
    # differential oracle and for callers who know their tree is parse-heavy.
    if parallel is None and (sink_mode or n_files < _PARALLEL_MIN_FILES):
        return 0
    cpus = _os.cpu_count() or 1
    return min(cpus, 8) if cpus > 1 else 0


def _p1_worker(item: tuple[str, str]):
    """Pass 1 for one file: parse + collect defs into a fresh container and return
    the exact five fields `_collect_defs` mutates (all picklable)."""
    rel, path_str = item
    try:
        tree = ast.parse(Path(path_str).read_text(encoding="utf-8"))
        mini = _Project(root=Path("."))
        _collect_defs(mini, rel, Path(path_str), tree)
        return ("ok", rel, mini.nodes, mini.exported_names, mini.main_calls,
                mini.module_consts, mini.fixture_names)
    except (SyntaxError, UnicodeDecodeError, OSError, RecursionError) as exc:
        return ("skip", rel, type(exc).__name__)


def _p2_worker(item: tuple[str, str]):
    """Pass 2 for one file against the fork-inherited symbol table. Returns the
    exact deltas the serial pass produces: edges, pass-2 nodes (`_global_state`'s
    VARIABLE nodes), and framework-base class ids (`_walk_scope`). A mid-file
    RecursionError keeps the partial edges, matching the serial guard."""
    proj = _WPROJ
    assert proj is not None
    rel, path_str = item
    try:
        tree = ast.parse(Path(path_str).read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError, RecursionError):
        return [], [], set()  # vanished/changed since pass 1 (race) — serial parity
    proj.edges = []           # process-local (COW): collect this file's edges only
    n0 = len(proj.nodes)
    ebc0 = set(proj.external_base_classes)
    try:
        _collect_edges(proj, rel, tree)
    except RecursionError:
        pass  # keep the partial edges, exactly like the serial except-branch
    return proj.edges, proj.nodes[n0:], proj.external_base_classes - ebc0


def extract_project(root: str | Path,
                    ignore: list[str] | None = None, *,
                    cache_asts: bool = True,
                    edge_sink: object = None,
                    skip_sink: list[tuple[str, str]] | None = None,
                    fallback: object = None,
                    parallel: bool | None = None,
                    project_sink: dict | None = None) -> tuple[list[Node], list[Edge]]:
    """Two passes: (1) collect definitions + symbol table, (2) resolve references.

    `ignore` is a list of globs (relative to root) to skip — e.g. migrations.

    `cache_asts` (streaming, v2 work): when True (default) every file's AST is held in
    memory between the two passes — fastest, but all ASTs are co-resident at peak. When
    False, ASTs are dropped after pass 1 and the file is *re-parsed* in pass 2 — trading ~2x
    parse CPU for a much lower memory peak (no all-ASTs-resident step). The produced
    (nodes, edges) are IDENTICAL either way (same deterministic parse); the only observable
    difference is peak RSS and CPU. Verified by the streaming differential oracle.

    `edge_sink`: when given, edges are pushed to it after EACH file of pass 2 (and the
    accumulator freed), so the full edge list never materialises — previously only the
    tree-sitter extractor streamed, and a large pure-Python repo (Home Assistant: ~10k
    files whose `get`/`async_*` homonym fan-out yields tens of millions of AMBIGUOUS
    edges) OOM'd at ~7 GB despite streaming=True (field report, 2026-07-03). The INHERITS
    subset is teed into `proj.inherits` for the post-edge passes; override widening
    (`_propagate_overrides`), which needs the whole edge set, is SKIPPED in sink mode —
    the caller must run its store twin (`Store._propagate_overrides`) after nodes land,
    as `_reindex_streaming` does. Rows are identical either way (sorted-row differential
    oracle); only insertion order differs.
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

    workers = _parallel_workers(len(files), parallel, edge_sink is not None)
    # Parallel mode never caches ASTs (trees can't cross process boundaries);
    # pass 2 re-parses, exactly like the streaming path — result identical.
    parsed: dict[str, ast.Module] | None = {} if (cache_asts and not workers) else None
    ok_files: list[tuple[str, Path]] = []  # (rel, path) of files whose defs were collected
    syntax_failed: list[str] = []  # rels ast.parse REJECTED (syntax newer than interpreter)
    if workers:
        import multiprocessing as mp
        items = [(p.relative_to(proj.root).as_posix(), str(p)) for p in files]
        with mp.get_context("fork").Pool(workers) as pool:
            # imap preserves submission order, so every merge below happens in
            # sorted-file order — byte-identical to the serial loop.
            for res in pool.imap(_p1_worker, items, chunksize=16):
                if res[0] == "skip":
                    _, rel, why = res
                    if skip_sink is not None:
                        skip_sink.append((rel, why))
                    if why == "SyntaxError":
                        syntax_failed.append(rel)
                    continue
                _, rel, f_nodes, f_exported, f_mains, f_consts, f_fixtures = res
                proj.nodes.extend(f_nodes)
                # _sym_add keeps the global sets and the per-file symtab record in
                # lockstep; the worker's mini-project processed exactly one file,
                # so its global sets ARE that file's contribution.
                _sym_add(proj, rel, "export", f_exported)
                _sym_add(proj, rel, "main", f_mains)
                _sym_add(proj, rel, "const", f_consts)
                _sym_add(proj, rel, "fixture", f_fixtures)
                ok_files.append((rel, proj.root / rel))
        files = []  # consumed; the serial loop below is skipped
    for path in files:
        rel = path.relative_to(proj.root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            _collect_defs(proj, rel, path, tree)
        except (SyntaxError, UnicodeDecodeError, OSError, RecursionError) as exc:
            # Skip the one file, never abort the whole reindex (panel DDD/OOO).
            # OSError: a broken symlink / unreadable file (submodules, races).
            # RecursionError: a pathologically deep AST — a huge flat expression
            # (generated SQL/HTML/string builders) overflows ast.parse or the walk;
            # one bad file must not leave the entire DB empty.
            # But NEVER skip silently (research/18 bug 1: 880 PEP 695 files — 10% of
            # Home Assistant — vanished with no signal): report every skip to the
            # caller so it can surface the gap and/or hand SyntaxError files to the
            # tree-sitter Python fallback.
            if skip_sink is not None:
                skip_sink.append((rel, type(exc).__name__))
            if isinstance(exc, SyntaxError):
                syntax_failed.append(rel)
            if parsed is not None:
                parsed.pop(rel, None)
            continue
        if parsed is not None:
            parsed[rel] = tree
        ok_files.append((rel, path))
        # streaming (cache_asts=False): `tree` falls out of scope here and is freed, so the
        # ASTs are never all co-resident; pass 2 re-parses each ok_file below.

    # Python-fallback stitching (research/18 round 2 — v3.37.1): a file ast.parse
    # rejected (syntax newer than this interpreter) is re-extracted by the caller's
    # `fallback` hook (the tree-sitter Python grammar). Its NODES must join the
    # symbol table BEFORE `_index`, or every cross-boundary reference is invisible:
    # v3.37.0 bolted the rescued files on AFTER this extractor finished, so a call
    # from a normal file into a rescued one resolved against nothing and was
    # DROPPED (not even a hole — `_ref_edges` drops unknown names as unreliable).
    # On Home Assistant the rescued files include core.py — the hub everything
    # calls through — and audit_graph recall collapsed from 0.975 to 0.299. With
    # the nodes indexed here, attribute calls, receiver calls, imports and homonym
    # widening all resolve into rescued files through the ordinary rules.
    fallback_edges: list[Edge] = []
    if syntax_failed and fallback is not None:
        fnodes, fallback_edges = fallback(sorted(syntax_failed))  # type: ignore[operator]
        proj.nodes.extend(fnodes)

    _index(proj)
    _apply_entrypoint_roles(proj)
    _apply_script_roles(proj)
    _seed_entrypoint_classes(proj)

    # The rescued files' own edges: keep tree-sitter's in-bucket resolutions, but
    # re-resolve its HOLES (calls/references/inherits into code it couldn't see)
    # against the now-complete symbol table via the standard name-based rules
    # (INFERRED single candidate / AMBIGUOUS homonym fan-out). A name still
    # unknown stays a hole — find_holes semantics unchanged.
    for e in fallback_edges:
        if e.dst_id is None and e.dst_symbol and e.relation in (
                Relation.CALLS, Relation.REFERENCES, Relation.INHERITS):
            loc = e.location.rsplit(":", 2)
            e_rel = loc[0] if len(loc) == 3 else e.src.split("::", 1)[0]
            line = int(loc[1]) if len(loc) == 3 and loc[1].isdigit() else 0
            # Resolves through the standard rules or is DROPPED — an unknown name
            # here is a builtin/stdlib/external call, and both extractors' contract
            # is that such call holes are unreliable and never emitted.
            _ref_edges(proj, e.src, e.dst_symbol, e.relation, e_rel, line, is_method=True)
        else:
            proj.edges.append(e)

    def _drain() -> None:
        # Streaming: push accumulated edges to the sink and free them, tee-ing the INHERITS
        # subset (all the post-edge passes need) into proj.inherits. Keeps peak RAM at one
        # file's fan-out instead of the whole repo's. No-op without a sink.
        if edge_sink is None:
            return
        for e in proj.edges:
            if e.relation is Relation.INHERITS:
                proj.inherits.append(e)
            edge_sink.append(e)  # type: ignore[attr-defined]
        proj.edges.clear()

    _drain()  # edges emitted between the passes (entry-point / script-role seeds)
    if workers:
        # Fork NOW: children snapshot the fully-built symbol table (post-_index,
        # post-seeds, post-fallback-stitching) copy-on-write. The parent merges
        # each file's (edges, pass-2 nodes, framework-base ids) in submission
        # order, draining to the sink per file exactly like the serial loop.
        # No cross-file pass-2 dependency exists: `var::` VARIABLE nodes never
        # enter the symbol table even serially.
        import multiprocessing as mp
        global _WPROJ
        _WPROJ = proj
        try:
            with mp.get_context("fork").Pool(workers) as pool:
                for f_edges, f_nodes, f_ebc in pool.imap(
                        _p2_worker, [(rel, str(p)) for rel, p in ok_files],
                        chunksize=8):
                    proj.nodes.extend(f_nodes)
                    proj.external_base_classes.update(f_ebc)
                    proj.edges.extend(f_edges)
                    _drain()
        finally:
            _WPROJ = None
    for rel, path in (ok_files if not workers else ()):
        if parsed is not None:
            tree = parsed[rel]
        else:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError, RecursionError):
                continue  # vanished/changed since pass 1 (race) -> skip, as a parse error would
        try:
            _collect_edges(proj, rel, tree)
        except RecursionError:
            _drain()
            continue  # same pathological-depth guard for the edge pass (panel OOO)
        _drain()
    if edge_sink is None:
        # In-memory mode: populate the tee by one scan so the post-edge passes read
        # `proj.inherits` uniformly in both modes (identical contents by construction).
        proj.inherits = [e for e in proj.edges if e.relation is Relation.INHERITS]
    _apply_callback_roles(proj)
    _seed_test_classes(proj)
    _seed_exported_inherited_methods(proj)
    _seed_protocol_dunders(proj)
    if edge_sink is None:
        _propagate_overrides(proj)
    else:
        # Override widening needs the full edge set; in sink mode the caller runs the
        # DB-backed twin (Store._propagate_overrides) once nodes are inserted. Drain the
        # dunder-seed edges appended above.
        _drain()
    if project_sink is not None:
        # The cross-file state the store persists (research/21): the per-file raw
        # name-sets plus the file-listing-derived import-internality inputs.
        project_sink["symtab"] = proj.symtab
        project_sink["packages"] = sorted(proj.packages)
        project_sink["source_prefix"] = proj.source_prefix
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
    inh = [(e.src, e.dst_id) for e in proj.inherits
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
    for e in proj.inherits:  # the INHERITS tee — survives streaming's per-file drain
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


# IPython / Jupyter "rich display" protocol methods: single-underscore (so NOT dunders), invoked
# BY NAME by IPython when an object is displayed (a notebook cell value, `display(obj)`), never from
# source. A class implementing them — and whatever they reach — is otherwise false-flagged dead
# (rich dogfood: `JupyterMixin._repr_mimebundle_`, panel R90). Enumerated by the IPython
# rich-display docs; the analogue of the interpreter dunders below.
_IPYTHON_PROTOCOL = frozenset({
    "_repr_html_", "_repr_markdown_", "_repr_svg_", "_repr_png_", "_repr_jpeg_", "_repr_latex_",
    "_repr_json_", "_repr_javascript_", "_repr_pdf_", "_repr_pretty_", "_repr_mimebundle_",
    "_ipython_display_", "_ipython_key_completions_",
})

# Enum machinery hooks: single-underscore (so NOT dunders), invoked BY NAME by the enum
# metaclass — `_missing_` on a failed value lookup (`Color(x)` with no matching member) and
# `_generate_next_value_` by `auto()`. Like the IPython hooks, neither has an explicit call
# site, so a live enum's hooks (and the helpers they alone reach) are otherwise false-flagged
# dead (sqlalchemy/werkzeug dogfood + Python manual pass, cardinal). The names are
# enum-specific enough to tie unconditionally, matching the IPython-hook treatment.
_ENUM_HOOKS = frozenset({"_missing_", "_generate_next_value_"})


def _is_protocol_method(name: str) -> bool:
    """A method invoked implicitly by name, not from source: an interpreter dunder (`__call__`,
    `__getitem__`, …), an IPython/Jupyter rich-display hook (`_repr_html_`, `_repr_mimebundle_`),
    or an Enum machinery hook (`_missing_`, `_generate_next_value_`)."""
    return (len(name) > 4 and name.startswith("__") and name.endswith("__")) \
        or name in _IPYTHON_PROTOCOL \
        or name in _ENUM_HOOKS


def _seed_protocol_dunders(proj: _Project) -> None:
    """Tie each implicitly-invoked method's liveness to its class. A dunder is invoked by the
    interpreter (`instance()` -> `__call__`; attribute access on a descriptor -> `__get__`/`__set__`;
    `obj[k]` -> `__getitem__`; `with obj` -> `__enter__`; etc.) and an IPython rich-display hook
    (`_repr_html_`/`_repr_mimebundle_`/…) is invoked by name by IPython on display — neither has an
    explicit call site, so the method (and a helper it alone calls) is orphaned and confidently
    flagged dead once the class is in use (panels R20A/R90, cardinal). Add a REFERENCES edge class ->
    method so that when the class is reachable, these (and their callees) are too.

    Scoped to the class: a dead class's hooks stay dead (no over-rooting)."""
    class_ids = {cid for ids in proj.class_by_name.values() for cid in ids}
    for node in proj.nodes:
        name = node.name
        if node.kind is NodeKind.METHOD and "." in node.id and _is_protocol_method(name):
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
    for e in proj.inherits:  # the INHERITS tee (identical to scanning proj.edges here)
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
    for e in proj.inherits:  # the INHERITS tee — survives streaming's per-file drain
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
        _sym_add(proj, rel, "export", exported)
    _sym_add(proj, rel, "main", _main_block_calls(tree))
    for stmt in tree.body:  # module-level constants (not graphed as nodes)
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                # Flatten tuple/list unpacking (incl. starred): `HORIZONTAL, VERTICAL
                # = 1, 2` (django.contrib.admin.options) defines two module constants,
                # but only bare-Name targets were collected — imports of the unpacked
                # names then surfaced as phantom holes (the bulk of Django's
                # find_holes noise, research/19).
                stack = [t]
                while stack:
                    n = stack.pop()
                    if isinstance(n, ast.Name):
                        _sym_add(proj, rel, "const", n.id)
                    elif isinstance(n, (ast.Tuple, ast.List)):
                        stack.extend(n.elts)
                    elif isinstance(n, ast.Starred):
                        stack.append(n.value)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            _sym_add(proj, rel, "const", stmt.target.id)
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
                _sym_add(proj, rel, "export", nm)
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
                        _sym_add(proj, rel, "export", bound)
                        # Under a RENAMED re-export (`from .core import Engine as Public`),
                        # the bound public name (`Public`) differs from the actually-defined
                        # symbol's name (`Engine`). `_apply_entrypoint_roles` matches nodes by
                        # their defined name, so also register the original leaf — gated on
                        # the *bound* name being public — or the renamed re-export's target
                        # (and its methods) is flagged dead (panel R25A, cardinal). A private
                        # bound name (`import _hidden`) is skipped above, staying dead.
                        if isinstance(node, ast.ImportFrom):
                            _sym_add(proj, rel, "export", alias.name)
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
                        _sym_add(proj, rel, "export", ref)

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
        # Pytest fixture registry (v3.39.0, fixture-aware test rooting): a def
        # decorated @pytest.fixture / @fixture / @pytest_asyncio.fixture is
        # injected BY PARAMETER NAME into tests — record the name in pass 1 so
        # pass 2 can bind test/fixture parameters project-wide regardless of
        # file visit order (fixtures live in conftest.py up the tree).
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if _name_of(target) == "fixture":
                _sym_add(proj, rel, "fixture", node.name)
                break
        roles: set[str] = set()
        if is_test_file and node.name.startswith("test"):
            roles.add("test")
            kind = NodeKind.TEST
        elif is_test_file and not parent_is_class and _is_pytest_hook(node.name):
            # pytest plugin hooks (`pytest_configure`, `pytest_collection_modifyitems`, …) are
            # discovered and invoked BY NAME by pytest from conftest.py and other test-tree
            # modules — no in-tree call site, so they (and the helpers they reach) are otherwise
            # false-flagged dead. Scoped to test files (the `is_test_file` set) and the `pytest_`
            # prefix (pytest's own hook-discovery convention); over-rooting a stray dead
            # `pytest_*` in a test-tree helper is cardinal-safe.
            roles.add("callback")
        # A bodyless abstract / Protocol interface method (`def m(self): ...` under
        # @abstractmethod or inside a Protocol/ABC) is an API contract fulfilled by overrides,
        # never called by name — so it was false-flagged dead though it defines live interface
        # surface (#86). Root it `callback`. Cardinal-safe (only adds a root); a method with a
        # real body (a concrete default in an ABC) stays dead-eligible.
        if _is_abstract(node, in_abstract) and _is_stub(node):
            roles.add("callback")
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
    _instance_state(proj, rel, tree)


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


# In-place mutators of the stdlib containers (research/22: a CLOSED allowlist —
# an unknown method emits nothing; a missed loop is acceptable, a phantom loop
# that cries wolf is not).
_MUTATING_METHODS = frozenset({
    "append", "extend", "add", "update", "setdefault", "pop", "popitem",
    "insert", "remove", "clear", "discard", "appendleft", "extendleft",
})
_CONTAINER_CALLS = frozenset({
    "dict", "list", "set", "defaultdict", "deque", "Counter", "OrderedDict",
})


def _is_container_value(v: ast.AST) -> bool:
    return isinstance(v, (ast.Dict, ast.List, ast.Set)) or (
        isinstance(v, ast.Call) and _name_of(v.func) in _CONTAINER_CALLS)


def _module_containers(tree: ast.Module) -> set[str]:
    """Module-level names bound to a container literal/constructor — the shared
    state that gets MUTATED without any `global` statement (`CACHE[k] = v`,
    `REGISTRY.append(x)`). Top-level statements only, mirroring the
    module-constants scan."""
    out: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and _is_container_value(stmt.value):
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
                and stmt.value is not None and _is_container_value(stmt.value)):
            out.add(stmt.target.id)
    return out


def _sub_root(node: ast.AST) -> str | None:
    """The root Name of a subscript/attribute chain (`X[k][j]` / `X.attr[k]` -> X)."""
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _global_state(proj: _Project, rel: str, tree: ast.Module) -> None:
    """Model mutable module-level state for data-loop detection (design §6.F).

    Two triggers (research/22):
    * a name some function declares `global` (intent to REBIND shared state —
      the original v1 slice), and
    * a module-level CONTAINER (dict/list/set literal or constructor) that some
      function MUTATES in place — subscript store/delete or a known mutating
      method call — which needs no `global` statement at all and is the
      dominant shared-state idiom (`CACHE[k] = v`, `REGISTRY.append(fn)`).

    Emits WRITES (writer -> var) and READS (reader -> var); `find_data_loops`
    closes the loop through CALLS. Container var nodes are emitted only when
    some function actually writes — a read-only module table is configuration,
    not feedback state, and must not flood the graph.
    """
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            declared.update(node.names)
    containers = _module_containers(tree)
    tracked = declared | containers
    if not tracked:
        return
    per_func: list[tuple[str, int, set[str], set[str]]] = []  # (fid, line, writes, reads)
    for func, fid in _iter_funcs(tree, rel):
        decl: set[str] = set()
        reads: set[str] = set()
        stores: set[str] = set()
        mut_writes: set[str] = set()
        mut_receivers: set[int] = set()  # id() of receiver Name nodes: not READS
        for child in _direct_nodes(func):
            if isinstance(child, ast.Global):
                decl.update(child.names)
            elif (isinstance(child, ast.Subscript)
                    and isinstance(child.ctx, (ast.Store, ast.Del))):
                root = _sub_root(child)
                if root in containers:
                    mut_writes.add(root)
                    n = child.value
                    while isinstance(n, (ast.Subscript, ast.Attribute)):
                        n = n.value
                    mut_receivers.add(id(n))
            elif (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr in _MUTATING_METHODS
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id in containers):
                mut_writes.add(child.func.value.id)
                mut_receivers.add(id(child.func.value))
        for child in _direct_nodes(func):
            if isinstance(child, ast.Name) and child.id in tracked:
                if isinstance(child.ctx, ast.Load) and id(child) not in mut_receivers:
                    reads.add(child.id)
                elif isinstance(child.ctx, ast.Store):
                    stores.add(child.id)
        # A WRITES edge requires the function to actually *assign* a declared global,
        # not merely declare it: `global x; return x` (read-only) must not get a
        # spurious WRITES (which faked a read+write data feedback loop in scan()).
        # A rebind of a container name WITHOUT `global` is a local shadow, not a
        # write to module state — only in-place mutations count for containers.
        writes = (decl & stores) | mut_writes
        if writes or reads:
            per_func.append((fid, func.lineno, writes, reads))

    written = {n for _, _, w, _ in per_func for n in w}
    # Declared-global names keep their original always-emit contract; container
    # names must earn the node with at least one write somewhere in the module.
    for name in sorted(declared | (written & containers)):
        proj.nodes.append(Node(id=f"var::{rel}::{name}", kind=NodeKind.VARIABLE,
                               name=name, location=f"{rel}:0:0"))
    emitted = declared | (written & containers)
    for fid, line, writes, reads in per_func:
        for name in writes & emitted:
            proj.edges.append(Edge(src=fid, relation=Relation.WRITES, dst_symbol=name,
                                   dst_id=f"var::{rel}::{name}", weight=1.0,
                                   provenance=Provenance.EXTRACTED,
                                   location=f"{rel}:{line}:0", source="ast"))
        for name in reads & emitted:  # a read is a read whether or not it also writes
            proj.edges.append(Edge(src=fid, relation=Relation.READS, dst_symbol=name,
                                   dst_id=f"var::{rel}::{name}", weight=1.0,
                                   provenance=Provenance.EXTRACTED,
                                   location=f"{rel}:{line}:0", source="ast"))


def _instance_state(proj: _Project, rel: str, tree: ast.Module) -> None:
    """Instance/class-attribute data loops (research/22 deliverable 2): methods
    of ONE class reading and writing the same `self.<attr>` form the classic
    non-global feedback shape (a worker that appends to `self.queue` and a
    drain method that consumes it and re-triggers the worker). Emits
    `var::<rel>::<Class>.<attr>` VARIABLE nodes with WRITES from methods that
    assign or in-place-mutate the attribute and READS from methods that load
    it — `self` receivers only (no alias chasing), class-scoped ids (never
    cross-class), and a node only when some method WRITES and another reads
    (a write-only or read-only attribute is not feedback state). Advisory as
    ever: READS/WRITES stay outside liveness."""

    def scan_class(cls: ast.ClassDef, class_qual: str) -> None:
        per_method: list[tuple[str, int, set[str], set[str]]] = []
        for stmt in cls.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            mid = Node.make_id(rel, f"{class_qual}.{stmt.name}")
            writes: set[str] = set()
            reads: set[str] = set()
            receivers: set[int] = set()
            for child in _direct_nodes(stmt):
                # self.attr = ... / self.attr[k] = ... / del self.attr[k]
                if (isinstance(child, (ast.Attribute, ast.Subscript))
                        and isinstance(child.ctx, (ast.Store, ast.Del))):
                    n: ast.AST = child
                    while isinstance(n, ast.Subscript):
                        n = n.value
                    if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                            and n.value.id == "self"):
                        writes.add(n.attr)
                        receivers.add(id(n))
                # self.attr.append(...) — in-place mutation via the allowlist
                elif (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr in _MUTATING_METHODS
                        and isinstance(child.func.value, ast.Attribute)
                        and isinstance(child.func.value.value, ast.Name)
                        and child.func.value.value.id == "self"):
                    writes.add(child.func.value.attr)
                    receivers.add(id(child.func.value))
            for child in _direct_nodes(stmt):
                if (isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load)
                        and isinstance(child.value, ast.Name) and child.value.id == "self"
                        and id(child) not in receivers):
                    reads.add(child.attr)
            if writes or reads:
                per_method.append((mid, stmt.lineno, writes, reads))
        written = {a for _, _, w, _ in per_method for a in w}
        read = {a for _, _, _, r in per_method for a in r}
        # __init__'s seeding write alone is not feedback; require a writer AND a
        # reader among the methods (they may be the same method — that IS a loop).
        feedback = written & read
        for attr in sorted(feedback):
            proj.nodes.append(Node(
                id=f"var::{rel}::{class_qual}.{attr}", kind=NodeKind.VARIABLE,
                name=f"{class_qual}.{attr}", location=f"{rel}:{cls.lineno}:0"))
        for mid, line, writes, reads in per_method:
            for attr in sorted(writes & feedback):
                proj.edges.append(Edge(
                    src=mid, relation=Relation.WRITES, dst_symbol=attr,
                    dst_id=f"var::{rel}::{class_qual}.{attr}", weight=1.0,
                    provenance=Provenance.EXTRACTED,
                    location=f"{rel}:{line}:0", source="ast"))
            for attr in sorted(reads & feedback):
                proj.edges.append(Edge(
                    src=mid, relation=Relation.READS, dst_symbol=attr,
                    dst_id=f"var::{rel}::{class_qual}.{attr}", weight=1.0,
                    provenance=Provenance.EXTRACTED,
                    location=f"{rel}:{line}:0", source="ast"))

    def walk(node: ast.AST, parent: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qual = f"{parent}.{child.name}" if parent else child.name
                scan_class(child, qual)
                walk(child, qual)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{parent}.{child.name}" if parent else child.name
                walk(child, qual)
            else:
                walk(child, parent)  # control-flow blocks aren't a scope

    walk(tree, "")


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
                name = _base_name(base)
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
            # Protocol dunders the source never NAMES (v3.39.0, research/18's recall
            # tail): `with` -> __enter__/__exit__ (async: __aenter__/__aexit__),
            # `for .. in` -> __iter__/__next__ (async: __aiter__/__anext__),
            # subscripts -> __getitem__/__setitem__/__delitem__. One shared `seen`
            # set bounds the per-function fan to one fallback per dunder.
            proto_seen: set[str] = set()
            for cm, is_async in _direct_withs(child):
                _with_edges(proj, rel, cid, class_qual, local_types, cm,
                            is_async=is_async, seen=proto_seen)
            for expr, dunders, line in _direct_protocol_uses(child):
                _protocol_dunder_edges(proj, rel, cid, local_types, expr,
                                       dunders, line, proto_seen)
            # getattr(recv, f"_prefix_{x}") dispatch (v3.39.0, research/18-19).
            _getattr_dispatch_edges(proj, rel, cid, child)
            # Fixture-aware test rooting (v3.39.0 — research/18's zero-recall
            # tests): pytest injects fixtures BY PARAMETER NAME, so a test whose
            # only static edges point at its own nested helpers reaches ALL its
            # real setup through parameters. Bind each parameter that names a
            # known @pytest.fixture def (pass-1 registry — conftest chains
            # included by construction) through the standard name-based rules;
            # fixtures request other fixtures the same way, so fixture defs get
            # the same binding. Builtin fixtures (tmp_path, monkeypatch, …) are
            # not project defs and bind to nothing.
            if proj.fixture_names and (
                    child.name.startswith("test") or child.name in proj.fixture_names):
                args = child.args
                for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                    if a.arg in proj.fixture_names:
                        _ref_edges(proj, cid, a.arg, Relation.CALLS, rel,
                                   child.lineno, is_method=True)
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


def _protocol_receiver_classes(proj: _Project, local_types: dict[str, str],
                               expr: ast.AST) -> list[str]:
    """Class node ids a protocol receiver expression resolves to: a direct
    constructor call (`with Lock():`) or a name with a declared local type."""
    if isinstance(expr, ast.Call):
        ctor = _name_of(expr.func)
        if ctor:
            return proj.class_by_name.get(ctor, [])
    elif isinstance(expr, ast.Name) and expr.id in local_types:
        return proj.class_by_name.get(local_types[expr.id], [])
    return []


def _protocol_dunder_edges(proj: _Project, rel: str, src_id: str,
                           local_types: dict[str, str], expr: ast.AST,
                           dunders: tuple[str, ...], line: int,
                           seen: set[str]) -> None:
    """The protocol-method resolver core (v3.39.0 — the largest slice of the
    recall tail measured in research/18: `TemplateContextManager.__exit__` was
    executed by 389 of 2,056 HA tests and statically reached by none). A `with`
    block, a `for` loop, or a subscript runs a dunder the source never names,
    so no call pass can see it. Resolution follows the house two-tier pattern:

    - receiver resolvable (constructor call / declared local type) -> EXACT
      references to that class's dunders (the pre-v3.39 `_with_edges` path);
    - receiver unknown -> the same name-based fallback every unknown-receiver
      call/read gets (`_ref_edges`, is_method=True): INFERRED single candidate,
      AMBIGUOUS homonym fan across the classes that define the dunder.
      Cardinal-safe (only ever adds reachability); a builtin receiver (dict/
      list subscripts — the overwhelmingly common case) resolves to no project
      symbol and adds nothing.

    `seen` dedupes per (function, dunder): fifty dict lookups in one function
    emit ONE `__getitem__` fan, not fifty (`_dedup_edges` would collapse the
    final rows anyway; this bounds the transient list)."""
    cls_ids = _protocol_receiver_classes(proj, local_types, expr)
    if cls_ids:
        for cid in cls_ids:
            for dunder in dunders:
                mid = f"{cid}.{dunder}"
                if mid in proj.ids and f"exact:{mid}" not in seen:
                    seen.add(f"exact:{mid}")
                    _add_ref(proj, src_id, dunder, mid, rel, line)
        return
    for dunder in dunders:
        if dunder not in seen:
            seen.add(dunder)
            _ref_edges(proj, src_id, dunder, Relation.REFERENCES, rel, line,
                       is_method=True)


def _with_edges(proj: _Project, rel: str, src_id: str, class_qual: str | None,
                local_types: dict[str, str], item: ast.withitem,
                is_async: bool = False, seen: set[str] | None = None) -> None:
    """A `with` context manager uses __enter__/__exit__ (`async with`:
    __aenter__/__aexit__) — reference them so they aren't flagged dead, and the
    cleanup they call (e.g. close) stays live."""
    dunders = ("__aenter__", "__aexit__") if is_async else ("__enter__", "__exit__")
    _protocol_dunder_edges(proj, rel, src_id, local_types, item.context_expr,
                           dunders, getattr(item.context_expr, "lineno", 0),
                           seen if seen is not None else set())


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


def _getattr_pattern(arg: ast.AST) -> tuple[str, str] | None:
    """(prefix, suffix) of a dynamic attribute-name expression with exactly one
    variable part — the shapes real dispatch code uses (research/18 tail,
    confirmed on Django in research/19):

        getattr(self, f"_async_step_{action}")     # f-string
        getattr(self, "as_%s" % connection.vendor)  # %-format
        getattr(self, "_get_" + kind + "_perms")    # concat (single var)
        getattr(obj, "handle_{}".format(kind))      # str.format

    Returns None when the shape doesn't match or the literal anchor is too
    short to select anything (len(prefix)+len(suffix) < 3 — a bare f"{x}"
    matches every symbol and would fan the whole graph)."""
    prefix = suffix = None
    if isinstance(arg, ast.JoinedStr):
        vals = arg.values
        consts = [v for v in vals if isinstance(v, ast.Constant)]
        holes = [v for v in vals if not isinstance(v, ast.Constant)]
        if len(holes) == 1 and len(consts) == len(vals) - 1:
            prefix = vals[0].value if isinstance(vals[0], ast.Constant) else ""
            suffix = vals[-1].value if isinstance(vals[-1], ast.Constant) else ""
    elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod) \
            and isinstance(arg.left, ast.Constant) and isinstance(arg.left.value, str) \
            and arg.left.value.count("%s") == 1 and "%" not in arg.left.value.replace("%s", ""):
        prefix, suffix = arg.left.value.split("%s")
    elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        left, right = arg.left, arg.right
        if isinstance(left, ast.Constant) and isinstance(left.value, str):
            # "_get_" + kind  (or "_get_" + kind + "_perms": left-nested Add)
            prefix, suffix = left.value, ""
        elif isinstance(left, ast.BinOp) and isinstance(left.op, ast.Add) \
                and isinstance(left.left, ast.Constant) and isinstance(left.left.value, str) \
                and isinstance(right, ast.Constant) and isinstance(right.value, str):
            prefix, suffix = left.left.value, right.value
        elif isinstance(right, ast.Constant) and isinstance(right.value, str):
            prefix, suffix = "", right.value
    elif isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) \
            and arg.func.attr == "format" and isinstance(arg.func.value, ast.Constant) \
            and isinstance(arg.func.value.value, str) \
            and arg.func.value.value.count("{}") == 1:
        prefix, suffix = arg.func.value.value.split("{}")
    if prefix is None or suffix is None:
        return None
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        return None
    if len(prefix) + len(suffix) < 3:
        return None
    return prefix, suffix


def _getattr_dispatch_edges(proj: _Project, rel: str, src_id: str,
                            func: ast.AST) -> None:
    """The getattr-dispatch heuristic (v3.39.0): `getattr(recv, f"_step_{x}")`
    invokes SOME member matching `_step_*`, but no call pass can name it —
    research/18 measured `_ScriptRun._async_step_*` handlers missed by dozens
    of HA tests, and research/19 found the same shape twice in Django
    (`as_%s` vendor methods, `_get_%s_permissions`). Reference every project
    function/method matching the literal prefix+suffix through the standard
    name-based rules (INFERRED/AMBIGUOUS). Deliberately NOT receiver-scoped:
    the matching member routinely lives on a base class or mixin in another
    file. Cardinal-safe — only ever adds reachability; a pattern with a
    too-short anchor is rejected in `_getattr_pattern`."""
    seen: set[tuple[str, str]] = set()
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2):
            continue
        pat = _getattr_pattern(node.args[1])
        if pat is None or pat in seen:
            continue
        seen.add(pat)
        prefix, suffix = pat
        for name in proj.by_name:
            if (name.startswith(prefix) and name.endswith(suffix)
                    and len(name) > len(prefix) + len(suffix)):
                _ref_edges(proj, src_id, name, Relation.REFERENCES, rel,
                           node.lineno, is_method=True)


def _direct_protocol_uses(func: ast.AST) -> list[tuple[ast.AST, tuple[str, ...], int]]:
    """(receiver_expr, dunders, line) for the implicit-dispatch sites in a
    function body other than `with` (which `_direct_withs` collects): `for`
    loops (incl. comprehension iterables) and subscript load/store/delete.
    Skips nested defs exactly like the other _direct_* collectors."""
    out: list[tuple[ast.AST, tuple[str, ...], int]] = []

    # Checks NODE ITSELF, then recurses — the check-children-only shape misses a
    # statement that is itself the match (`for x in reg:` as a direct body stmt),
    # the same latent gap fixed in _direct_withs (v3.39.0).
    def rec(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, ast.For):
            out.append((node.iter, ("__iter__", "__next__"), node.lineno))
        elif isinstance(node, ast.AsyncFor):
            out.append((node.iter, ("__aiter__", "__anext__"), node.lineno))
        elif isinstance(node, ast.comprehension):
            dunders: tuple[str, ...] = (("__aiter__", "__anext__") if node.is_async
                                        else ("__iter__", "__next__"))
            out.append((node.iter, dunders, getattr(node.iter, "lineno", 0)))
        elif isinstance(node, ast.Subscript):
            sub: tuple[str, ...]
            if isinstance(node.ctx, ast.Store):
                sub = ("__setitem__",)
            elif isinstance(node.ctx, ast.Del):
                sub = ("__delitem__",)
            else:
                sub = ("__getitem__",)
            out.append((node.value, sub, node.lineno))
        for child in ast.iter_child_nodes(node):
            rec(child)

    for stmt in getattr(func, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        rec(stmt)
    return out


def _direct_withs(func: ast.AST) -> list[tuple[ast.withitem, bool]]:
    out: list[tuple[ast.withitem, bool]] = []

    # Checks NODE ITSELF, then recurses. The old check-children-only shape never
    # collected a `with` that is a DIRECT body statement of the function — only
    # withs nested inside try/if/for were seen, so the top-level `with ctx:` (the
    # most common shape) emitted no __enter__/__exit__ edges at all. Latent since
    # the original _with_edges; exposed by the v3.39.0 protocol-resolver tests.
    def rec(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            is_async = isinstance(node, ast.AsyncWith)
            out.extend((item, is_async) for item in node.items)
        for child in ast.iter_child_nodes(node):
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


def _base_name(node: ast.AST) -> str | None:
    # A base class can be a subscripted generic (`class Sub(Base[K, V])`): the AST
    # is an ast.Subscript whose `.value` holds the real base expression. Unwrap it
    # so the INHERITS edge (and external-base detection) resolves `Base` instead of
    # None — otherwise the subclass has no parent edge, polymorphic overrides of the
    # base's template methods are reached by nothing, and live code is flagged dead
    # (cardinal). Confirmed on sqlalchemy/werkzeug `Mixin(Base[K, V])` patterns.
    # Loop (not a single unwrap) so a nested subscript (`Base[K][V]`) still resolves.
    while isinstance(node, ast.Subscript):
        node = node.value
    return _name_of(node)


def _is_pytest_hook(name: str) -> bool:
    """A pytest plugin hook function: pytest discovers and invokes functions named with the
    documented `pytest_` prefix (`pytest_configure`, `pytest_collection_modifyitems`, …) by
    name from conftest.py / plugin modules, with no in-tree call site."""
    return name.startswith("pytest_") and len(name) > len("pytest_")


def _has_registration_decorator(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if a decorator is a call or attribute access (`@app.callback()`, `@app.route("/")`,
    `@foo.register`) — the idiom that *registers* a function for its side effect and supplies its
    behaviour externally, so an empty body is intentional, not an unimplemented stub. A bare-name
    decorator (`@property`, `@staticmethod`) does not qualify."""
    return any(isinstance(d, (ast.Call, ast.Attribute)) for d in func.decorator_list)


def _is_stub(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [s for s in func.body if not _is_docstring(s)]
    empty = False
    if not body:
        empty = True
    elif len(body) == 1:
        only = body[0]
        # An explicit `raise NotImplementedError` is always a stub, even when decorated.
        if isinstance(only, ast.Raise) and _raises_notimplemented(only):
            return True
        empty = isinstance(only, ast.Pass) or (
            isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant)
            and only.value.value is Ellipsis)
    if not empty:
        return False
    # A pass/…/docstring-only body under a registration decorator is idiomatic (the decorator
    # carries the behaviour) — NOT an unimplemented stub. Self-analysis dogfood: a Typer
    # `@app.callback(...)` group callback with a `pass` body was RED-flagged as a live stub.
    return not _has_registration_decorator(func)


def _is_abstract(func: ast.FunctionDef | ast.AsyncFunctionDef, in_abstract: bool) -> bool:
    for d in func.decorator_list:
        if _name_of(d) in {"abstractmethod", "abstractproperty"}:
            return True
    return in_abstract


def _is_abstract_class(node: ast.ClassDef) -> bool:
    # Use _base_name (not _name_of) so a SUBSCRIPTED base is recognized: `class Repo(Protocol[T])`
    # / `class C(ABC, Generic[T])` — the base is an ast.Subscript whose `_name_of` is None, so the
    # class was not detected as abstract and its bodyless members were treated as implementation
    # stubs / flagged dead instead of interface contracts (#70). _base_name unwraps the subscript.
    bases = {_base_name(b) for b in node.bases}
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
    # Any .py nested at least one directory under `src/` (`src/<pkg>/...`) means `<pkg>` is a
    # package living under the source root. Do NOT require `<pkg>/__init__.py`: a PEP 420
    # namespace package has none, yet is still imported as `<pkg>` — requiring __init__.py
    # left namespace-package src-layouts undetected, so their absolute imports stayed external
    # and module-load-only-live code was flagged dead (panel R42A, cardinal). A loose
    # `src/m.py` (no package dir, one slash) does NOT trigger it.
    has_pkg_under_src = any(r.startswith("src/") and r.count("/") >= 2 for r in rels)
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
    # Root-anchored gitignore-style semantics live in core/globs.py — PurePath.match's
    # right-anchored, non-recursive-** behaviour mis-ignored in BOTH directions on the
    # 2026-07-05 Home Assistant field run (research/18 bug 2).
    from ..globs import ignored
    return ignored(path.relative_to(root), ignore)
