# stitchgraph v2.1.23 — Java anonymous-inner-class override in a class-scope initializer (#62)

A cardinal fix for Java: an anonymous-inner-class override declared in a field/static initializer is
no longer flagged dead.

## The bug

```java
abstract class Handler { protected abstract void handle(); }

public class M {
    static final Handler H = new Handler() {     // anonymous class in a FIELD initializer
        protected void handle() { work(); }      // override — invoked polymorphically via H
    };
    private static void work() {}                // called only by the override
}
```

`handle` is live — it is invoked through `H` by whatever holds the `Handler` reference — and `work`
is live through it. Both were confidently flagged dead.

An anonymous class has **no name**, so its override can never be resolved by a `Class.method`
by-name call; it is reachable only:

1. via the **containment edge** from a live enclosing function (when the anonymous class sits inside
   a method body) — already handled; or
2. via **polymorphic dispatch** through the base type — not modeled.

In a field / static initializer there is no enclosing function, so path (1) doesn't apply and the
override is orphaned. Public overrides were masked by the `exported` role (Java `public` methods are
auto-rooted), so the gap surfaced on `protected` / package-private overrides of a custom abstract
base.

## The fix

A def that sits directly in an anonymous class body — a `class_body` that is the child of an
`object_creation_expression` — is now rooted `callback` when it is at **class scope**
(`enclosing_func is None`). Such a member is polymorphically invoked and unreachable by name, so
rooting it is correct.

- **In-method anonymous classes keep their precision:** when the anonymous class is inside a method
  body the member is *not* `callback`-rooted — it stays reachability-gated via the existing
  containment edge, so an override inside a genuinely-dead method still flags dead.
- **Named classes are untouched:** the check requires the `object_creation_expression` parent, so a
  genuinely-dead method of a normal named class still flags.
- **Cardinal-safe:** the change only ever adds a root.
- The private helper a class-scope override alone calls becomes live transitively (the override is
  rooted and its body is walked), so it needs no separate rooting.

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Known limitations

Abstract / interface method **declarations** (no body) are still flagged dead even when concrete
implementations are reached (#86). This is pre-existing and general (it happens for named subclasses
too) and is **not** a true live-code cardinal — an abstract declaration is a bodyless contract slot,
so flagging it hides no executable code. Tracked as a precision follow-up. The C/C++ macro-body call
sites (#59) and cross-TU function-table promotion (#69) remain deferred.

## Quality gate

Full suite — 503 tests (protected anon override in a field initializer + JDK `Runnable` / custom
abstract base, both rooted; an in-method anon override in a *dead* method still flags; a dead
named-class method still flags) + ruff + mypy clean; differential oracle suite (27) green; mutation
meta-oracle on `_is_anonymous_class_member` (5/5 killed); two-round full-diversity multi-model
adversarial review — no in-scope cardinal, no crash.
