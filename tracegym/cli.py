"""The `tg` command line. Subcommands are wired up as the phases land."""

from __future__ import annotations

import typer

from tracegym import __version__

app = typer.Typer(
    name="tg",
    help="TraceGym: record, replay, score, and gate AI agents.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Record, replay, score, and gate AI agents at $0."""


@app.command()
def version() -> None:
    """Print the installed TraceGym version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
