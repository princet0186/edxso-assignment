"""Command-line entry point: `uv run outreach --help`."""

import logging
import signal
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from sqlmodel import Session

from outreach.config import SECRET_NAMES_BY_PURPOSE, load_brand, load_secrets, load_settings
from outreach.db import get_engine, init_db
from outreach.exports import OUTPUTS_DIR, write_deliverables
from outreach.personalize.review import approve_all_valid
from outreach.pipeline import Stage, run_pipeline
from outreach.reporting import build_funnel, error_count, recent_runs
from outreach.sending.dispatch import send_queued_emails
from outreach.sending.smtp import SmtpMailer

ALL_STAGES = "all"
RECENT_RUNS_SHOWN = 5
API_DEFAULT_HOST = "127.0.0.1"
API_DEFAULT_PORT = 8000
SIGTERM_EXIT_CODE = 143
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
    # `kill` sends SIGTERM, which ends the process without a Python exception; turning it into
    # SystemExit lets the pipeline record the run as FAILED instead of leaving it RUNNING.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(SIGTERM_EXIT_CODE))


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


@app.command("approve")
def approve_validated() -> None:
    """Approve every draft that passed all validators.

    A demo helper; the console's review queue is the normal path. Drafts with open
    validation issues are never approved.
    """
    with Session(get_engine()) as session:
        approved = approve_all_valid(session, load_settings().campaign_id)
    console.print(f"Approved {approved} validated drafts.")


@app.command()
def send() -> None:
    """Send (or simulate, per SEND_MODE) every approved email in the queue, batch by batch."""
    settings, secrets, brand = load_settings(), load_secrets(), load_brand()
    console.print(f"Send mode: [bold]{secrets.send_mode}[/bold]")
    totals: dict[str, int] = {}
    with Session(get_engine()) as session:
        while True:
            outcomes = send_queued_emails(
                session,
                settings,
                secrets,
                brand,
                lambda: SmtpMailer(secrets, settings.sending.smtp_timeout_seconds),
            )
            if not outcomes:
                break
            for status, count in outcomes.items():
                totals[status] = totals.get(status, 0) + count
    console.print(f"Delivery outcomes: {totals or 'nothing to send'}")


@app.command()
def export() -> None:
    """Write the dataset, messages, tracker and run summary to outputs/."""
    with Session(get_engine()) as session:
        counts = write_deliverables(session, load_settings(), OUTPUTS_DIR)
    console.print(f"Wrote {OUTPUTS_DIR}: {counts}")


@app.command()
def api(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = API_DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="Port to listen on.")] = API_DEFAULT_PORT,
) -> None:
    """Start the HTTP API used by the n8n workflows."""
    import uvicorn  # imported here: only this command needs the server

    uvicorn.run("outreach.api.main:app", host=host, port=port)
