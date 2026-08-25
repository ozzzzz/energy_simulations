"""The static report: same figures as the dashboard, but on disk and offline."""

import re

import pytest

from app.simulations.sim0.engine import run_scenario
from app.simulations.sim0.report import build_report_html, write_report

EXTERNAL_TAG = re.compile(r"<(?:script|link|img)[^>]*(?:src|href)\s*=\s*[\"']?(?:https?:)?//", re.I)


@pytest.fixture(scope="module")
def run():
    return run_scenario("inference", duration_s=3600.0, dt=60.0)


def test_the_report_is_self_contained(run) -> None:
    df, kpis = run
    html = build_report_html(df, kpis, "inference")

    assert html.startswith("<!doctype html>")
    match = EXTERNAL_TAG.search(html)
    assert match is None, f"external reference: {match.group(0)}"
    # plotly.js is inlined, and only once.
    assert "Plotly.newPlot" in html
    assert html.count("plotly.js v") <= 1


def test_every_kpi_reaches_the_page(run) -> None:
    df, kpis = run
    html = build_report_html(df, kpis, "inference")
    for label in ("Uptime", "Energy", "Delivery efficiency", "Liquid capacity"):
        assert label in html, label


def test_all_four_figures_are_present(run) -> None:
    df, kpis = run
    html = build_report_html(df, kpis, "inference")
    # One <div id="..." class="plotly-graph-div"> per figure.
    assert html.count("plotly-graph-div") == 4


def test_an_incident_scenario_gets_a_section_per_window() -> None:
    """The dashboard offers three windows for a cooling incident; so does the file."""
    df, kpis = run_scenario("cooling_failure", duration_s=6 * 86400.0, dt=300.0)
    html = build_report_html(df, kpis, "cooling_failure")

    assert "During cooling incident" in html
    assert html.count("plotly-graph-div") == 12


def test_a_scenario_without_an_incident_has_a_single_window(run) -> None:
    df, kpis = run
    html = build_report_html(df, kpis, "inference")
    assert "During cooling incident" not in html


def test_writing_creates_parent_directories(run, tmp_path) -> None:
    df, kpis = run
    path = write_report(df, kpis, "inference", tmp_path / "deep" / "nested" / "report.html")
    assert path.exists()
    assert path.stat().st_size > 0


def test_the_cli_writes_both_artifacts(tmp_path) -> None:
    from typer.testing import CliRunner

    from app.cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "sim0-run",
            "--scenario",
            "training",
            "--duration",
            "3600",
            "--dt",
            "60",
            "--output",
            str(tmp_path / "s" / "training.csv"),
            "--html",
            str(tmp_path / "s" / "training.html"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "s" / "training.csv").exists()
    assert (tmp_path / "s" / "training.html").exists()
    assert "uptime_pct" in result.stdout
