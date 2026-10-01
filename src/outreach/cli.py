"""Command-line entry point: `uv run outreach --help`."""

import logging
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from sqlmodel import Session

from outreach.config import SECRET_NAMES_BY_PURPOSE, load_secrets, load_settings
from outreach.db import get_engine, init_db
from outreach.pipeline import Stage, run_pipeline
from outreach.reporting import build_funnel, error_count, recent_runs

ALL_STAGES = "all"
RECENT_RUNS_SHOWN = 5
# HTTP client libraries log every request at INFO; that drowns out pipeline progress.
NOISY_HTTP_LOGGERS = ("httpx", "httpx2", "openai")

app = typer.Typer(no_args_is_help=True, help="Micro-influencer discovery and outreach pipeline.")
console = Console()


@app.callback()
def configure_logging(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    for noisy_logger in NOISY_HTTP_LOGGERS:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


@app.command("init-db")
def init_database() -> None:
    """Create the SQLite database and any missing tables (safe to re-run)."""
    init_db(get_engine())
    console.print("[green]Database ready.[/green]")


@app.command("check-env")
def check_env() -> None:
    """Show which secrets are configured — never their values."""
    secrets = load_secrets()
    table = Table("Purpose", "Variable", "Status")
    for purpose, names in SECRET_NAMES_BY_PURPOSE.items():
        for name in names:
            status = "[green]set[/green]" if getattr(secrets, name) else "[red]missing[/red]"
            table.add_row(purpose, name.upper(), status)
    console.print(table)
    console.print(f"Send mode: [bold]{secrets.send_mode}[/bold]")


def parse_stages(stages: str) -> list[Stage]:
    if stages == ALL_STAGES:
        return list(Stage)
    try:
        return [Stage(name.strip()) for name in stages.split(",")]
    except ValueError as exc:
        valid = ", ".join(stage.value for stage in Stage)
        raise typer.BadParameter(f"Unknown stage. Valid: {valid} or '{ALL_STAGES}'") from exc


@app.command()
def run(
    stages: Annotated[str, typer.Option(help="Comma-separated stages, or 'all'.")] = ALL_STAGES,
    limit: Annotated[
        int | None,
        typer.Option(help="Process at most N items per stage (queries for discover)."),
    ] = None,
) -> None:
    """Run pipeline stages. Each stage is resumable; re-running skips finished work."""
    engine = get_engine()
    init_db(engine)
    finished_run = run_pipeline(engine, parse_stages(stages), item_limit=limit)
    console.print(f"Run {finished_run.id}: [bold]{finished_run.status}[/bold]")
    summary()


@app.command()
def summary() -> None:
    """Print the pipeline funnel and recent runs."""
    engine = get_engine()
    init_db(engine)
    with Session(engine) as session:
        funnel = Table("Funnel step", "Count", title="Pipeline funnel")
        for step in build_funnel(session, load_settings()):
            funnel.add_row(step.label, str(step.count))
        console.print(funnel)

        runs = Table("Run", "Status", "Stages", "Quota units", "Started (UTC)", title="Recent runs")
        for past_run in recent_runs(session, RECENT_RUNS_SHOWN):
            runs.add_row(
                str(past_run.id),
                past_run.status,
                ", ".join(past_run.stages),
                str(past_run.youtube_quota_used),
                past_run.started_at.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(runs)
        console.print(f"Recorded per-creator errors: {error_count(session)}")
