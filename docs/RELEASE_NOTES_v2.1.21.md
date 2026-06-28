# stitchgraph v2.1.21 — Go method value / method expression references (#49)

A cardinal fix for Go: an **unexported** method reached only by *referencing* it (not calling it)
is now kept live.

## The bug

```go
package main

type t struct{}
func (x t) run() {}              // unexported method
func reg(f func()) {}

func main() {
    v := t{}
    reg(v.run)                   // method VALUE — v.run is referenced, never called here
}
```

`run` is live — it is handed to `reg` as a callback — but it was confidently flagged dead. The same
held for the *method expression* form (`use(t.run)`, the unbound `T.method`) and for a method value
stored in a struct-literal field (`cfg{onRun: v.run}`).

The cause: stitchgraph saw method **calls** (`v.run()`) via `_direct_calls`, but the **reference**
pass `_direct_refs` only collected `identifier` / `type_identifier` / `constant` / `name` nodes. A
Go selector's method part (`run` in `v.run`) is a `field_identifier`, so it was never emitted — the
method received no inbound edge and fell out of the reachable set.

Capitalized (exported) methods are rooted as public API, so this only ever surfaced on unexported
methods — which is exactly where it matters, since those are the ones dead-code analysis is meant to
reason about.

## The fix

`_direct_refs` now emits the trailing `field` name of a Go `selector_expression` as a by-name
`REFERENCES` edge. `selector_expression` is unique to the Go grammar, so the change is naturally
scoped to Go.

- A method named as a value/expression now keeps its target live.
- A plain struct-field access (`v.name`) that happens to share a name with a function is
  cardinal-safe over-rooting — by-name references resolve only to in-project symbols, and
  over-rooting never produces a false-dead.
- A `v.run()` **call** contains the same selector the reference pass now reads, but the edge loop
  dedups `REFERENCES` against the `CALLS` set, so a called method is never double-counted.
- A genuinely-unused unexported method still flags dead (verified by regression).

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Known limitations (unchanged, deferred)

The same "method named as a value, not called" shape in other grammars — Rust `Foo::method` /
`vec.iter().map(Foo::bar)`, C# method groups, JS `arr.forEach(obj.handler)` — is pre-existing and
tracked separately; each warrants its own per-language review. JS/TS bare function/arrow expressions
in expression positions (#83) and `this.#m()` private dispatch (#76/#78) remain deferred.

**Precision note (cardinal-safe).** A plain struct-field read (`w.run`) is syntactically identical to
a method value, so its field name is emitted as a reference too. A genuinely-dead function/method
whose name *exactly* collides with a struct field is therefore over-rooted (kept live). This is never
a false-dead — it is the precision-over-recall, cardinal-safe direction, and strictly better than the
pre-fix state where the method value itself was false-dead — and it is bounded to exact-name
collisions. A follow-up (#84) will narrow selector-field references to method-kind resolution to
recover the lost recall.

## Quality gate

Full suite — 493 tests (Go method value / method expression / struct-field value parametrized,
the call-not-double-counted check, and an over-rooting guard that a genuinely-dead unexported method
still flags) + ruff + mypy clean; differential oracle suite (27) green; mutation meta-oracle on
`_direct_refs` — the new `selector_expression` site killed; two-round full-diversity multi-model
adversarial review — no in-scope cardinal, no crash.
