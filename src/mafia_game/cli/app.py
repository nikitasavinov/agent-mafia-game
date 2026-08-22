"""The `mafia` command-line entry point.

Commands live in sibling modules and register themselves here.
"""

from __future__ import annotations

import typer

from .doctor import register as register_doctor
from .play import register as register_play
from .replay import register as register_replay
from .runs import register as register_runs
from .stats import register as register_stats

app = typer.Typer(
    name="mafia",
    help="Run Mafia games with autonomous agents from different models.",
    no_args_is_help=True,
)
runs_app = typer.Typer(
    help="List and search recorded runs.",
    no_args_is_help=True,
)

register_doctor(app)
register_play(app)
register_replay(app)
register_runs(runs_app)
register_stats(app)
app.add_typer(runs_app, name="runs")


if __name__ == "__main__":
    app()
