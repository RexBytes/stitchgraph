# Oracle tests

Differential / invariant oracles that own the **regression tail** so review panels can
spend their budget on novelty (see `CONTRIBUTING.md` → "The differential-oracle harness"
and `docs/TESTING.md`). They are plain pytest modules — run with the rest of the suite or
in isolation:

```bash
pytest tests/oracles            # just the oracles (seconds)
pytest                          # whole suite incl. oracles
```

Each oracle leans on something that already exists, so it stays cheap (≤ ~50 lines of
logic; if one balloons, find the chokepoint invariant instead):

| File | Shape | Ground truth it leans on |
|---|---|---|
| `test_corrupt_store.py` | chokepoint invariant | the row mappers are the one path row→object; every mapped field must match its dataclass type. Column list is schema-derived. |
| `test_incremental_differential.py` | differential | full `reindex` is ground truth; the incremental `replace_file` path must agree on find_stale + fan_in + holes. Corpus = the real `src/` tree. |
| `test_cardinal_matrix.py` | parametrized matrix | reachable-by-construction ⟹ never flagged dead, across scope × use-kind cells. |

**When a panel finds a new class, extend the oracle that should own it** (a new column is
already covered by the schema-derived corrupt-store oracle; a new edit shape → a cell in
the differential; a new use/scope → a cell in the matrix). A panel finding that only gets
a point regression test is incomplete.

**Oracles rot** when the architecture moves (a chokepoint relocates, an invariant
changes). Re-validate with mutation testing (catches a blinded oracle) and the
parallel-site lint (catches a moved chokepoint). See `docs/TESTING.md` → "How this
evolves".
