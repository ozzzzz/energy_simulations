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


if __name__ == "__main__":
    cli()
