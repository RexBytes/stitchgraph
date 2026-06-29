# stitchgraph v2.1.10 — Python IPython/Jupyter display-protocol hooks (rich dogfood)

Found by **dogfooding the real `rich` library** — the kind of bug a synthetic probe misses but a real
codebase surfaces.

## The bug

Indexing `rich` (15.0.0) flagged `JupyterMixin._repr_mimebundle_` and `JupyterRenderable._repr_mimebundle_`
as dead. These belong to the **IPython/Jupyter rich-display protocol** — `_repr_html_`, `_repr_markdown_`,
`_repr_svg_`, `_repr_png_`, `_repr_jpeg_`, `_repr_latex_`, `_repr_json_`, `_repr_javascript_`,
`_repr_pdf_`, `_repr_pretty_`, `_repr_mimebundle_`, `_ipython_display_`, `_ipython_key_completions_` —
which IPython invokes *by name* when displaying an object (a notebook cell value, `display(obj)`),
never from source.

stitchgraph already ties a class's **dunders** (`__call__`, `__getitem__`, `__enter__`, …) to the
class so their implicit invocation keeps them (and their callees) live. But the IPython protocol
methods are *single*-underscore (`_repr_…_`), so the `__x__` dunder rule missed them — a live display
hook, and whatever it reached, was false-flagged dead.

## The fix

`_seed_protocol_dunders` now also recognizes the IPython display-protocol names (via a shared
`_is_protocol_method` helper) and seeds the same class → method `REFERENCES` edge. When the class is
reachable, its display hooks and their callees are live; a dead class's hooks stay dead
(cardinal-safe — tied to the class, not rooted unconditionally). The set is the *documented* IPython
rich-display protocol, not an open-ended `_repr_*` match (`_repr_custom_` is not in it).

On `rich`, `_repr_mimebundle_` is now live; the `find_stale` candidate count drops from 38 to 36 (the
remaining 36 are the library's genuinely-public API — the documented "can't see external callers"
advisory, not bugs).

## Compatibility

No API or schema change; indexes rebuild cleanly. Python indexes now keep a class's IPython display
hooks (and their callees) live when the class is reachable.

## Quality gate

Full suite (incl. a regression test asserting live-class hooks/callees live + dead-class hooks dead,
and a helper test) + ruff + mypy clean; differential oracles green; mutation meta-oracle over
`_is_protocol_method` (all mutants killed); two-round full-diversity multi-model adversarial review.
