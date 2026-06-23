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
    op_params = op.params()

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
    params = []
    for p in op_params:
        if p.default is inspect.Parameter.empty:
            params.append(inspect.Parameter(p.name, p.kind, annotation=str))
        else:
            params.append(inspect.Parameter(
                p.name, inspect.Parameter.KEYWORD_ONLY,
                default=typer.Option(p.default, help=f"{p.name}"), annotation=str))
    params.append(inspect.Parameter(
        "db", inspect.Parameter.KEYWORD_ONLY,
        default=typer.Option("stitchgraph.db", help="index database path"), annotation=str))
    params.append(inspect.Parameter(
        "json", inspect.Parameter.KEYWORD_ONLY,
        default=typer.Option(False, "--json", help="emit the raw envelope as JSON"),
        annotation=bool))
    command.__signature__ = inspect.Signature(params)
    command.__name__ = op.name
    return command


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
