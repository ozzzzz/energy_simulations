import json
import subprocess
import webbrowser
from pathlib import Path

import typer

from app.simulations.sim1.engine import run_scenario as sim1_run_scenario
from app.simulations.sim1.report import write_artifacts, write_comparison
from app.simulations.sim1.scenarios import design_margins, get_scenario, scenario_names
from app.simulations.sim1.video import VideoSpec, record

cli = typer.Typer(pretty_exceptions_enable=False)


def _warn_if_truncated(scenario_name: str, duration: float | None) -> None:
    """Overriding the duration can cut a scenario off before its events fire.

    Several sim1 scenarios schedule their failure for midday, so that it lands on
    the daily traffic peak. A 2 h override then produces a run that looks
    perfectly healthy for the wrong reason, which is worth a word rather than a
    silent surprise.
    """
    if duration is None:
        return
    events = get_scenario(scenario_name).events
    missed = [event for event in events if event.t >= duration]
    if missed:
        typer.secho(
            f"warning: --duration {duration:.0f}s cuts {scenario_name} short of "
            f"{len(missed)} scheduled event(s), the first at {missed[0].t:.0f}s "
            f"({missed[0].described()}). Drop --duration to use the scenario's own timescale.",
            err=True,
            fg=typer.colors.YELLOW,
        )


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
    html: str = typer.Option(None, help="Optional self-contained HTML report path (the dashboard's graphs, static)"),
) -> None:
    """Run a sim0 scenario headlessly and print KPIs."""
    import json
    import pathlib

    from app.simulations.sim0.engine import run_scenario
    from app.simulations.sim0.report import write_report

    df, kpis = run_scenario(scenario, duration_s=duration, dt=dt)
    if output:
        path = pathlib.Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        typer.echo(f"Time series written to {path}")
    if html:
        report = write_report(df, kpis, scenario, html)
        typer.echo(f"Report written to {report} ({report.stat().st_size / 1024:.0f} KB)")
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


@cli.command("sim1-list")
def sim1_list() -> None:
    """List sim1 scenarios with their default timescale."""
    typer.echo(f"{'scenario':22s} {'duration':>10s} {'dt':>7s} {'dt fine':>8s}  what it shows")
    for name in scenario_names():
        scenario = get_scenario(name)
        hours = scenario.default_duration_s / 3600.0
        typer.echo(
            f"{name:22s} {hours:9.1f}h {scenario.default_dt_s:6.0f}s "
            f"{scenario.default_dt_fine_s:7.1f}s  {scenario.description}"
        )
    typer.echo("")
    typer.echo("Reference build:")
    for key, value in design_margins(get_scenario("normal").site).items():
        typer.echo(f"  {key:26s} {value:10.2f}")


@cli.command("sim1-run")
def sim1_run(
    scenario: str = typer.Option("normal", help="Scenario name; see sim1-list"),
    duration: float = typer.Option(None, help="Simulated seconds (default: the scenario's own)"),
    dt: float = typer.Option(None, help="Coarse tick, seconds"),
    dt_fine: float = typer.Option(None, help="Tick inside event windows; 0 disables refinement"),
    seed: int = typer.Option(None, help="Override the scenario seed"),
    out: str = typer.Option("out/sim1", help="Output root; writes <out>/<scenario>/"),
    viz: bool = typer.Option(True, help="Write the self-contained index.html"),
    analysis: bool = typer.Option(True, help="Write the Plotly analysis.html"),
    csv: bool = typer.Option(True, help="Write facility/racks/events CSVs"),
    viz_points: int = typer.Option(2500, help="Target points in the visualization payload"),
    open_browser: bool = typer.Option(False, "--open/--no-open", help="Open the visualization when done"),
) -> None:
    """Run a sim1 scenario, print its KPIs and write the artifacts."""
    config = get_scenario(scenario)
    _warn_if_truncated(scenario, duration)
    result = sim1_run_scenario(scenario, duration_s=duration, dt=dt, dt_fine=dt_fine, seed=seed)
    artifacts = write_artifacts(result, config, out, viz=viz, analysis=analysis, csv=csv, viz_points=viz_points)

    typer.echo(json.dumps(result.kpis, indent=2, allow_nan=False))
    for path in artifacts.written():
        typer.echo(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    if open_browser and artifacts.viz:
        webbrowser.open(artifacts.viz.resolve().as_uri())


@cli.command("sim1-viz")
def sim1_viz(
    scenario: str = typer.Option("normal", help="Scenario name; see sim1-list"),
    duration: float = typer.Option(None, help="Simulated seconds (default: the scenario's own)"),
    dt: float = typer.Option(None, help="Coarse tick, seconds"),
    dt_fine: float = typer.Option(None, help="Tick inside event windows"),
    seed: int = typer.Option(None, help="Override the scenario seed"),
    out: str = typer.Option("out/sim1", help="Output root; writes <out>/<scenario>/index.html"),
    viz_points: int = typer.Option(2500, help="Target points in the visualization payload"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the visualization when done"),
) -> None:
    """Build only the single-file visualization — fast iteration on the page."""
    config = get_scenario(scenario)
    _warn_if_truncated(scenario, duration)
    result = sim1_run_scenario(scenario, duration_s=duration, dt=dt, dt_fine=dt_fine, seed=seed)
    artifacts = write_artifacts(result, config, out, viz=True, analysis=False, csv=False, viz_points=viz_points)
    page = artifacts.viz
    if page is None:  # pragma: no cover - viz=True above guarantees a path
        raise RuntimeError("visualization was not written")
    typer.echo(f"wrote {page} ({page.stat().st_size / 1024:.0f} KB)")
    if open_browser:
        webbrowser.open(page.resolve().as_uri())


def _default_watermark() -> str:
    """Whoever is running this, as git knows them — overridable with --watermark."""
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return f"© {name}" if name else ""


@cli.command("sim1-video")
def sim1_video(
    scenario: str = typer.Option("cooling_failure", help="Scenario name; see sim1-list"),
    out: str = typer.Option("out/sim1", help="Output root; reads <out>/<scenario>/index.html"),
    output: str = typer.Option(None, help="MP4 path (default: <out>/<scenario>/<scenario>.mp4)"),
    seconds: float = typer.Option(90.0, help="Clip length; longer means each payload point is held longer"),
    fps: int = typer.Option(30, help="Frames per second"),
    width: int = typer.Option(1920, help="Video width"),
    height: int = typer.Option(1080, help="Video height"),
    watermark: str = typer.Option(None, help="Copyright line, bottom right; default: git user.name"),
    watermark_opacity: float = typer.Option(0.22, help="0 = invisible, 1 = solid"),
    timeline: bool = typer.Option(False, help="Keep the timeline ribbons; costs the diagram a third of the frame"),
    crf: int = typer.Option(20, help="x264 quality; lower is better and bigger"),
    scale: int = typer.Option(2, help="Capture at this pixel ratio, then downscale; 1 records ~3x faster"),
    build: bool = typer.Option(True, help="Run the scenario first; --no-build reuses index.html"),
    open_video: bool = typer.Option(False, "--open/--no-open", help="Open the MP4 when done"),
) -> None:
    """Record the sim1 visualization of a scenario as a watermarked MP4."""
    page = Path(out) / scenario / "index.html"
    if build:
        config = get_scenario(scenario)
        result = sim1_run_scenario(scenario)
        write_artifacts(result, config, out, viz=True, analysis=False, csv=False)
    elif not page.exists():
        raise typer.BadParameter(f"{page} does not exist; drop --no-build to produce it")

    spec = VideoSpec(
        seconds=seconds,
        fps=fps,
        width=width,
        height=height,
        watermark=_default_watermark() if watermark is None else watermark,
        watermark_opacity=watermark_opacity,
        timeline=timeline,
        scale=scale,
        crf=crf,
    )
    destination = Path(output) if output else page.parent / f"{scenario}.mp4"

    with typer.progressbar(length=spec.frames, label=f"recording {scenario}") as bar:
        done = 0

        def tick(number: int, _total: int) -> None:
            nonlocal done
            bar.update(number - done)
            done = number

        video = record(page, destination, spec, progress=tick)

    typer.echo(f"wrote {video} ({video.stat().st_size / 1_048_576:.1f} MB)")
    if open_video:
        webbrowser.open(video.resolve().as_uri())


@cli.command("sim1-compare")
def sim1_compare(
    scenarios: str = typer.Option(",".join(scenario_names()), help="Comma-separated scenario names"),
    duration: float = typer.Option(
        None, help="Override every scenario's duration, seconds (may cut scenarios short of their events)"
    ),
    out: str = typer.Option("out/sim1/compare", help="Output directory"),
) -> None:
    """Run several scenarios and compare their headline KPIs."""
    names = [name.strip() for name in scenarios.split(",") if name.strip()]
    results = []
    for name in names:
        typer.echo(f"running {name}...")
        _warn_if_truncated(name, duration)
        results.append(sim1_run_scenario(name, duration_s=duration))
    html_path, csv_path = write_comparison(results, out)
    typer.echo(Path(csv_path).read_text())
    typer.echo(f"wrote {html_path}")


if __name__ == "__main__":
    cli()
