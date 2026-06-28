# stitchgraph v2.1.22 — same-name method-overload role clobber (#61)

A store-level cardinal fix: two same-name method **overloads** no longer let one clobber the other's
entry-point roles.

## The bug

```java
public class Api {
    public void process() {}          // public API — rooted `exported`
    private void process(int x) {}    // private overload, declared LAST
}
```

Both overloads qualify to the same node id (`Api.process`). Node persistence used
`INSERT OR REPLACE`, so the **last** overload written — the private one, with no roles — replaced the
row wholesale and **erased the `exported` role** of the public method. `Api.process` then had no
root and was confidently flagged dead, though it is live public API.

The same shape bit framework-callback overloads:

```java
class Bean {
    @PostConstruct void init() {}     // lifecycle callback — rooted `callback`
    void init(int x) {}               // plain overload, declared LAST -> clobbers `callback`
}
```

The failure was **declaration-order-dependent**: only the last overload's roles survived, so simply
reordering the methods changed whether a live method was flagged dead.

## The fix

`Store.add_node` now performs an upsert — `INSERT ... ON CONFLICT(id) DO UPDATE` — that **unions the
roles** of colliding nodes rather than replacing them. A rooting role (`exported`, `callback`,
`test`, `runtime`, …) is never dropped, regardless of which overload is written last.

- **Cardinal-safe and order-independent:** the union only ever *adds* roles, so the rooted method
  stays live in either declaration order.
- **Edges were never at risk:** call/reference edges key on the `src` node id, so both overload
  bodies' edges were already retained — only the node *row's* roles were being lost. (That is also
  why the helper a method body calls stayed live even before this fix.)
- **Non-role columns** continue to take the newest row, matching the prior `REPLACE` semantics.
- **Joined-role duplicates are harmless** (every reader splits roles into a set) and bounded (a full
  reindex and `replace_file` both clear before re-inserting, so the union spans only one build's
  overloads).
- **Store-level, so it generalizes:** C# and C++ overloads get the same protection, not only Java.
- A genuinely-unused, distinctly-named private method still flags dead (verified) — the union does
  not leak roles across different ids.

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one (both paths persist through `add_node`).

## Known limitations (unchanged, deferred)

Java anonymous-inner-class JDK abstract overrides (#62) and the C/C++ macro-body call sites (#59) /
cross-TU function-table promotion (#69) remain pre-existing and deferred to their own per-language
reviews.

## Quality gate

Full suite — 498 tests (public/private and callback/plain overloads, both declaration orders,
Java + C#, plus an over-rooting guard that a distinctly-named dead private method still flags) + ruff
+ mypy clean; differential oracle suite (27) green; the role-union logic is pinned by non-vacuous
regression tests (4/5 fail on the prior `INSERT OR REPLACE`); two-round full-diversity multi-model
adversarial review — no in-scope cardinal, no crash.
