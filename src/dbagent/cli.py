"""db-agent <engine> <verb>. Each available engine is a sub-command group; planned engines are only listed."""

from __future__ import annotations

import typer

from . import __version__
from .engines import ENGINES

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="db-agent: read-only DBA toolkit. Run `db-agent engines` to see what is supported.")

for _e in ENGINES:
    if _e.status == "available":
        app.add_typer(_e.load(), name=_e.name, help=_e.summary)


@app.command()
def engines() -> None:
    """List database engines and whether they are implemented."""
    for e in ENGINES:
        typer.echo(f"{e.name:<12} {e.status:<10} {e.title}: {e.summary}")


@app.command()
def version() -> None:
    """Print the db-agent version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
