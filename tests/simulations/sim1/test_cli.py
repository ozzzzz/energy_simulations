"""One end-to-end smoke test through the CLI.

This single test walks engine -> facility -> every component -> telemetry -> kpis
-> analysis -> payload -> builder, which is why it is worth its runtime.
"""

import json

from typer.testing import CliRunner

from app.cli import cli

runner = CliRunner()


def test_sim1_run_writes_every_artifact(tmp_path) -> None:
    result = runner.invoke(
        cli,
        [
            "sim1-run",
            "--scenario",
            "grid_outage_gen_ok",
            "--duration",
            "2400",
            "--dt",
            "10",
            "--out",
            str(tmp_path),
            "--no-open",
            "--viz-points",
            "300",
        ],
    )
    assert result.exit_code == 0, result.stdout

    directory = tmp_path / "grid_outage_gen_ok"
    for name in ("index.html", "analysis.html", "facility.csv", "racks.csv", "events.csv", "kpis.json"):
        path = directory / name
        assert path.exists(), name
        assert path.stat().st_size > 0, name

    kpis = json.loads((directory / "kpis.json").read_text())
    assert kpis["uptime_pct"] == 100.0
    assert kpis["gen_starts"] == 1.0
    assert "__SIM_DATA__" not in (directory / "index.html").read_text()


def test_sim1_run_can_skip_the_expensive_outputs(tmp_path) -> None:
    result = runner.invoke(
        cli,
        [
            "sim1-run",
            "--scenario",
            "normal",
            "--duration",
            "600",
            "--out",
            str(tmp_path),
            "--no-viz",
            "--no-analysis",
            "--no-csv",
            "--no-open",
        ],
    )
    assert result.exit_code == 0, result.stdout
    directory = tmp_path / "normal"
    assert (directory / "kpis.json").exists()
    assert not (directory / "index.html").exists()
    assert not (directory / "analysis.html").exists()


def test_an_unknown_scenario_fails_loudly() -> None:
    result = runner.invoke(cli, ["sim1-run", "--scenario", "does_not_exist"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)


def test_a_short_duration_warns_that_events_were_skipped(tmp_path) -> None:
    """`cooling_failure` trips at 15 min and restores at 105 min, so a 20-minute
    override runs the failure but never the recovery — worth saying out loud."""
    result = runner.invoke(
        cli,
        [
            "sim1-run",
            "--scenario",
            "cooling_failure",
            "--duration",
            "1200",
            "--out",
            str(tmp_path),
            "--no-viz",
            "--no-analysis",
            "--no-csv",
            "--no-open",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cuts cooling_failure short" in result.stderr
    assert "Chiller restored" in result.stderr


def test_the_full_scenario_needs_no_warning(tmp_path) -> None:
    result = runner.invoke(
        cli,
        [
            "sim1-run",
            "--scenario",
            "user_surge",
            "--out",
            str(tmp_path),
            "--no-viz",
            "--no-analysis",
            "--no-csv",
            "--no-open",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "cuts" not in result.stderr
    kpis = json.loads((tmp_path / "user_surge" / "kpis.json").read_text())
    assert float(kpis["request_drop_pct"]) > 1.0
