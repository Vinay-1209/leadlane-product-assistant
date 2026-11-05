from __future__ import annotations

import logging
from typing import Optional

import typer

from assistant.ask import ask as run_ask
from assistant.ask import format_answer, format_timing
from assistant.catalogue import build_catalogue
from assistant.config import context_mode
from assistant.eval_run import print_report, run_eval
from assistant.ingest import TENANTS, ingest_all, ingest_tenant

app = typer.Typer(add_completion=False, no_args_is_help=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


@app.command("ingest")
def ingest_cmd(
    tenant: Optional[str] = typer.Option(
        None, "--tenant", "-t", help="alvery | nordhaven; default: both"
    ),
    path: Optional[str] = typer.Option(
        None,
        "--path",
        "-p",
        help="PDF file or directory (e.g. updates/alvery). Requires --tenant. "
        "Revision datasheets (*_rev*) supersede older Datasheet PDFs for the same article.",
    ),
) -> None:
    """Load PDFs into SQLite (OCR when a page has no text layer) and rebuild catalogue."""
    if path:
        if not tenant:
            raise typer.BadParameter("--path requires --tenant")
        if tenant not in TENANTS:
            raise typer.BadParameter(f"tenant must be one of {TENANTS}")
        n = ingest_tenant(tenant, path=path)
        typer.echo(f"Ingested {n} pages for tenant={tenant} from {path}")
        return
    if tenant:
        if tenant not in TENANTS:
            raise typer.BadParameter(f"tenant must be one of {TENANTS}")
        n = ingest_tenant(tenant)
        typer.echo(f"Ingested {n} pages for tenant={tenant}")
    else:
        counts = ingest_all()
        for t, n in counts.items():
            typer.echo(f"Ingested {n} pages for tenant={t}")


@app.command("catalogue")
def catalogue_cmd(
    tenant: Optional[str] = typer.Option(
        None, "--tenant", "-t", help="alvery | nordhaven; default: both"
    ),
) -> None:
    """Rebuild structured products/prices/kits from already-ingested pages."""
    targets = [tenant] if tenant else list(TENANTS)
    for t in targets:
        if t not in TENANTS:
            raise typer.BadParameter(f"tenant must be one of {TENANTS}")
        counts = build_catalogue(t)
        typer.echo(f"Catalogue tenant={t}: {counts}")


@app.command("ask")
def ask_cmd(
    question: str = typer.Argument(..., help="Question to ask"),
    tenant: str = typer.Option(..., "--tenant", "-t", help="Tenant id"),
) -> None:
    """Answer a question from one tenant's documents only."""
    if tenant not in TENANTS:
        raise typer.BadParameter(f"tenant must be one of {TENANTS}")
    ans = run_ask(tenant, question)
    typer.echo(format_answer(ans))
    typer.echo(
        f"\n[mode={ans.mode} pages={ans.pages_used} structured={ans.structured} "
        f"model={ans.model} cost≈${ans.cost_usd:.4f} "
        f"tokens={ans.prompt_tokens}+{ans.completion_tokens} "
        f"abstained={ans.abstained}]"
    )
    typer.echo(f"[{format_timing(ans)}]")


@app.command("eval")
def eval_cmd(
    phase: Optional[str] = typer.Option(
        "initial", "--phase", help="initial | after_update | all"
    ),
    only: Optional[str] = typer.Option(
        None, "--only", help="Comma-separated question ids"
    ),
) -> None:
    """Run the public eval set with deterministic judges."""
    typer.echo(f"CONTEXT_MODE={context_mode()}")
    phase_arg: str | None = None if phase == "all" else phase
    ids = [x.strip() for x in only.split(",")] if only else None
    rows = run_eval(phase=phase_arg, question_ids=ids)
    print_report(rows)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
