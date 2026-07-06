# 19 — Django 5.2.15 field analysis: findings for upstream

**Date:** 2026-07-05 · **Tool:** v3.38.0 (branch `claude/adjacency-sidecar`) ·
**Corpus:** django-5.2.15 sdist from PyPI (2,873 files / 47,431 nodes / 1.6 GB
index / 182 s / 357 MB flat / 1 PEP 695 fallback file) · **Method:** static
battery (orient, scan, find_stale, find_holes, chokepoints, subsystems,
find_component), then EVERY candidate hand-verified against the source before
being called a finding. Django is one of the most-audited Python codebases in
existence — the tool's word alone is never enough here.

## Finding 1 (reportable): `Atom1Feed` silently drops `stylesheets`

**Chain of evidence:** `find_stale` flagged `SyndicationFeed.add_stylesheets`
(feedgenerator.py:231). Verification showed the flag itself was right for an
interesting reason: the base hook has ZERO callers — `RssFeed.write` calls its
own override (:321), and **`Atom1Feed.write` (:422) never calls the hook at
all** — while the shared `SyndicationFeed.__init__` accepts and stores
`stylesheets=` for every feed class.

**Reproduction (django-5.2.15, stdlib only):**

```python
kw = dict(title="T", link="https://example.com/", description="D",
          stylesheets=["https://example.com/feed.xsl"])
Rss201rev2Feed(**kw).write(out, "utf-8")   # <?xml-stylesheet ...?> PRESENT
Atom1Feed(**kw).write(out, "utf-8")        # accepted, silently ABSENT
```

**Why it matters upstream:** the 5.2 release notes claim *"**All**
`SyndicationFeed` classes now support a `stylesheets` attribute. If specified,
an `<? xml-stylesheet ?>` processing instruction will be added"*
(docs/releases/5.2.txt:204) — the implementation satisfies this for RSS only.
The narrative docs (`ref/contrib/syndication.txt` "Feed stylesheets") are
RSS-focused, so this is EITHER a code gap (xml-stylesheet PIs are valid on any
XML document, Atom included — `Atom1Feed.write` should call the hook) or a
release-note/docstring overclaim (the base hook's docstring "Called from
write()." is untrue for Atom). Also: passing `stylesheets` to `Atom1Feed`
fails silently — no warning, no error.

**Caveat:** Django's Trac is unreachable from this environment (proxy); check
for an existing ticket before filing.

## Finding 2 (minor, reportable): dead private helper `LazySettings._show_deprecation_warning`

`django/conf/__init__.py:146`. Zero references anywhere in the tree (code,
tests, docs) — a leftover from settings-deprecation cycles whose shim
properties have since been removed. Underscore-private, so removal is
API-safe. Low-stakes cleanup PR material.

## The false-positive taxonomy (what the other 97 django/ candidates were)

Every remaining candidate fell into a known static-analysis boundary — and
two of them are *precisely* the dynamic-dispatch patterns the HA validation
(research/18) put on the resolver roadmap, now confirmed on a second codebase:

| bucket | examples | boundary |
|---|---|---|
| getattr dispatch | `ModelBackend._get_{user,group}_permissions` (via `"_get_%s_permissions" % from_name`), `DateFormat.N/U/W`, `as_mysql`/`as_postgresql` vendor methods | roadmap: getattr-dispatch heuristic |
| string-based attribute access | `is_postgresql_15` via `operator.attrgetter("is_postgresql_15")`, `supports_default_in_lead_lag` via `@skipUnlessDBFeature("...")` | same family |
| template boundary | `InlineAdminFormSet.is_collapsible` / `inline_formset_data` (used in `edit_inline/*.html` template variables), admin JS invoked from the DOM | Django-template variable resolver would close these |
| public-API-by-string | `PersistentRemoteUserMiddleware` (referenced from user settings strings), `AdminDocsConfig` (INSTALLED_APPS) | documented plugin-loader limitation |
| documented public API with no in-tree caller | `transaction.clean_savepoints`, `BaseDatabaseSchemaEditor.alter_db_tablespace`, `FormatStylePlaceholderCursor.arrayvar` | library surface; roots come from docs, not code |

## stitchgraph gaps this run exposed (our own backlog)

1. **Tuple-unpack module constants aren't collected** — `HORIZONTAL, VERTICAL
   = 1, 2` (admin/options.py) misses `module_consts`, so imports of those
   names surface as phantom holes (the bulk of find_holes' 112 items). Small
   extractor fix; added to STATUS.
2. `impact_of("get_response")` refused on a bare name that is only a
   parameter/attribute, never a def — correct behaviour, but the error could
   suggest `find_symbol` first.

## Battery timings (2,873 files / 1.6 GB index)

orient 9.7 s · find_stale 0.8 s · scan 13.8 s · chokepoints 5.1 s ·
subsystems 18.5 s · holes 1.4 s · find_component ≤5.8 s per query.
Everything interactive; nothing needed the known-cost path.
