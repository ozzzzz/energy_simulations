import json
import webbrowser
from pathlib import Path

import typer

from app.simulations.sim0.engine import run_scenario as sim0_run_scenario
from app.simulations.sim0.report import write_report
from app.simulations.sim1.engine import run_scenario as sim1_run_scenario
from app.simulations.sim1.models import VideoSpec
from app.simulations.sim1.report import write_artifacts
from app.simulations.sim1.scenarios import get_scenario, scenario_names
from app.simulations.sim1.video import record

cli = typer.Typer(pretty_exceptions_enable=False)

DEFAULT_WATERMARK = "© Bogdan Neterebskii"
"""Whose work the clips are. Overridable per run with --watermark."""


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


@cli.command("sim0-run")
def sim0_run(
    scenario: str = typer.Option("inference", help="Scenario: inference/training/mixed/cooling_failure"),
    duration: float = typer.Option(604800.0, help="Simulated duration, seconds (default: 1 week)"),
    dt: float = typer.Option(60.0, help="Tick size, seconds"),
    output: str = typer.Option(None, help="Optional CSV output path for the time series"),
    html: str = typer.Option(None, help="Optional self-contained HTML report path"),
) -> None:
    """Run a sim0 scenario headlessly and print KPIs."""
    df, kpis = sim0_run_scenario(scenario, duration_s=duration, dt=dt)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        typer.echo(f"Time series written to {path}")
    if html:
        report = write_report(df, kpis, scenario, html)
        typer.echo(f"Report written to {report} ({report.stat().st_size / 1024:.0f} KB)")
    typer.echo(json.dumps(kpis, indent=2))


@cli.command("sim1-run")
def sim1_run(
    scenario: str = typer.Option("normal", help=f"Scenario name, one of: {', '.join(scenario_names())}"),
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


@cli.command("sim1-video")
def sim1_video(
    scenario: str = typer.Option("cooling_failure", help=f"Scenario name, one of: {', '.join(scenario_names())}"),
    out: str = typer.Option("out/sim1", help="Output root; reads <out>/<scenario>/index.html"),
    output: str = typer.Option(None, help="MP4 path (default: <out>/<scenario>/<scenario>.mp4)"),
    seconds: float = typer.Option(90.0, help="Clip length; longer means each payload point is held longer"),
    fps: int = typer.Option(30, help="Frames per second"),
    width: int = typer.Option(1920, help="Video width"),
    height: int = typer.Option(1080, help="Video height"),
    watermark: str = typer.Option(None, help=f"Copyright line; default: {DEFAULT_WATERMARK}"),
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
        watermark=DEFAULT_WATERMARK if watermark is None else watermark,
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


if __name__ == "__main__":
    cli()
