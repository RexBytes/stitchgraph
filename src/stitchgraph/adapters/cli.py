"""CLI adapter (design §3). Built by introspecting core.operations so the CLI
maps 1:1 onto the library API — same names (kebab-cased), same params.

Typer is an optional dependency (`pip install stitchgraph[cli]`); imported lazily
so `import stitchgraph` never requires it.
"""

from __future__ import annotations

import inspect
import json as _json
from typing import Any

from ..core.operations import Operation, registry
from ..core.store import Store
from .render import render_text


def _require_typer():
    try:
        import typer
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit(
            "The CLI needs Typer. Install it with:  pip install 'stitchgraph[cli]'"
        ) from exc
    return typer


def build_app():
    typer = _require_typer()
    app = typer.Typer(add_completion=False, help="stitchgraph — code intelligence")

    for op in registry():
        app.command(name=op.name.replace("_", "-"), help=op.summary)(_make_command(typer, op))

    @app.command(name="watch", help="Re-index on file changes (Ctrl-C to stop).")
    def _watch(
        path: str = typer.Argument(".", help="repo root to watch"),
        db: str = typer.Option("stitchgraph.db", help="index database path"),
        interval: float = typer.Option(2.0, help="poll interval (seconds)"),
    ) -> None:
        import time

        from ..core import operations as ops
        from ..core.store import Store
        from ..core.watch import changed, snapshot

        with Store(db) as store:
            typer.echo(ops.reindex(store, path).meta)
            state = snapshot(path)
            typer.echo(f"watching {path} (every {interval}s)…")
            try:
                while True:
                    time.sleep(interval)
                    new = snapshot(path)
                    if changed(state, new):
                        state = new
                        typer.echo(f"change detected — reindexing… {ops.reindex(store, path).meta}")
            except KeyboardInterrupt:
                typer.echo("stopped")

    @app.command(name="report", help="Full Markdown report (orientation + issues + risk).")
    def _report(
        db: str = typer.Option("stitchgraph.db", help="index database path"),
        repo: str = typer.Option(".", help="repo root (for git risk)"),
    ) -> None:
        from .report import build_report
        typer.echo(build_report(db, repo))

    return app


def _make_command(typer, op: Operation):
    """Wrap an operation as a Typer command with the same caller-facing params."""
    op_params = op.exposed_params()

    def command(**kwargs: Any) -> None:
        db = kwargs.pop("db")
        as_json = kwargs.pop("json")
        with Store(db) as store:
            result = op.func(store, **kwargs)
        if as_json:
            typer.echo(_json.dumps(result.to_dict(), indent=2))
        else:
            typer.echo(render_text(op.name, result))
        raise typer.Exit(_exit_code(result))

    # Give the wrapper a signature Typer can introspect: the op's params + --db/--json.
    # Preserve each param's real type so Typer parses/validates it — rebuilding every
    # param as `str` made `--limit 5` arrive as "5" (crashing int comparisons) and
    # inverted bool flags.
    params = []
    for p in op_params:
        anno = _anno_type(p.annotation)
        if p.default is inspect.Parameter.empty:
            params.append(inspect.Parameter(p.name, p.kind, annotation=anno))
        else:
            params.append(inspect.Parameter(
                p.name, inspect.Parameter.KEYWORD_ONLY,
                default=typer.Option(p.default, help=f"{p.name}"), annotation=anno))
    params.append(inspect.Parameter(
        "db", inspect.Parameter.KEYWORD_ONLY,
        default=typer.Option("stitchgraph.db", help="index database path"), annotation=str))
    params.append(inspect.Parameter(
        "json", inspect.Parameter.KEYWORD_ONLY,
        default=typer.Option(False, "--json", help="emit the raw envelope as JSON"),
        annotation=bool))
    command.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    command.__name__ = op.name
    return command


_ANNO_TYPES = {"str": str, "int": int, "float": float, "bool": bool}


def _anno_type(annotation: Any) -> type:
    """Resolve an operation param's annotation to a concrete type for Typer.

    Annotations are JSON-simple (operations.exposed_params filters to those) but
    arrive as strings under `from __future__ import annotations`; default to `str`
    for anything unrecognised (e.g. `int | None`)."""
    if isinstance(annotation, type):
        return annotation if annotation in _ANNO_TYPES.values() else str
    if isinstance(annotation, str):
        base = annotation.replace("Optional[", "").replace("]", "").split("|")[0].strip()
        return _ANNO_TYPES.get(base, str)
    return str


def _exit_code(result) -> int:
    """scan-style exit codes (design §13.3): non-zero when red issues exist."""
    from ..core.envelope import Urgency
    if result.urgency is Urgency.RED:
        return 1
    return 0


def main() -> None:
    build_app()()


if __name__ == "__main__":
    main()
