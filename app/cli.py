import typer

cli = typer.Typer(pretty_exceptions_enable=False)


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv()


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload"),
) -> None:
    """Start the FastAPI server."""
    import uvicorn

    _load_env()
    uvicorn.run("app.server:local_factory", factory=True, host=host, port=port, reload=reload)


@cli.command()
def generate_openapi(
    output: str = typer.Option("docs/openapi.json", help="Output path"),
) -> None:
    """Generate OpenAPI schema to file."""
    import json
    import pathlib

    _load_env()
    from app.server import local_factory

    app = local_factory()
    schema = app.openapi()
    path = pathlib.Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2))
    typer.echo(f"OpenAPI schema written to {output}")


@cli.command("sim0-run")
def sim0_run(
    scenario: str = typer.Option("inference", help="Scenario: inference/training/mixed/cooling_failure"),
    duration: float = typer.Option(604800.0, help="Simulated duration, seconds (default: 1 week)"),
    dt: float = typer.Option(60.0, help="Tick size, seconds"),
    output: str = typer.Option(None, help="Optional CSV output path for the time series"),
) -> None:
    """Run a sim0 scenario headlessly and print KPIs."""
    import json

    from app.simulations.sim0.engine import run_scenario

    df, kpis = run_scenario(scenario, duration_s=duration, dt=dt)
    if output:
        df.to_csv(output, index=False)
        typer.echo(f"Time series written to {output}")
    typer.echo(json.dumps(kpis, indent=2))


@cli.command("sim0-dashboard")
def sim0_dashboard(
    scenario: str = typer.Option("inference", help="Scenario: inference/training/mixed/cooling_failure"),
    duration: float = typer.Option(604800.0, help="Simulated duration, seconds (default: 1 week)"),
    dt: float = typer.Option(60.0, help="Tick size, seconds"),
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8050, help="Bind port"),
) -> None:
    """Run a sim0 scenario and serve the energy-flow Dash viewer for it."""
    from app.simulations.sim0.dashboard import build_app
    from app.simulations.sim0.engine import run_scenario

    df, kpis = run_scenario(scenario, duration_s=duration, dt=dt)
    app = build_app(df, kpis, scenario)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    cli()
