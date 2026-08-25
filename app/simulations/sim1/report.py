"""Turn a run into files on disk, and several runs into a comparison.

Kept out of the CLI so the command layer stays declarative and this stays
testable without a ``CliRunner``.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from app.simulations.sim1.analysis import COLORS, LAYOUT, write_analysis
from app.simulations.sim1.engine import RunResult
from app.simulations.sim1.scenarios import ScenarioConfig
from app.simulations.sim1.viz.builder import write_html

COMPARE_KPIS: tuple[tuple[str, str], ...] = (
    # User-facing first: these are the numbers that say what a failure cost.
    ("request_drop_pct", "Requests dropped %"),
    ("slo_compliance_pct", "Within SLO %"),
    ("p95_queue_latency_s", "p95 queue wait s"),
    ("peak_utilisation_pct", "Peak compute %"),
    ("uptime_pct", "Uptime %"),
    ("served_pct", "IT served %"),
    ("pue_avg", "PUE"),
    ("unserved_energy_kwh", "Unserved kWh"),
    ("time_on_battery_s", "On battery s"),
    ("diesel_l", "Diesel L"),
    ("max_ups_load_pct_b", "Peak UPS B %"),
    ("total_cost_usd", "Total cost $"),
)


@dataclass(frozen=True, slots=True)
class Artifacts:
    directory: Path
    viz: Path | None = None
    analysis: Path | None = None
    facility_csv: Path | None = None
    racks_csv: Path | None = None
    events_csv: Path | None = None
    kpis_json: Path | None = None

    def written(self) -> list[Path]:
        return [
            p
            for p in (self.viz, self.analysis, self.facility_csv, self.racks_csv, self.events_csv, self.kpis_json)
            if p
        ]


def write_artifacts(
    result: RunResult,
    scenario: ScenarioConfig,
    out_dir: str | Path,
    viz: bool = True,
    analysis: bool = True,
    csv: bool = True,
    viz_points: int = 2500,
) -> Artifacts:
    directory = Path(out_dir) / result.scenario
    directory.mkdir(parents=True, exist_ok=True)

    kpis_path = directory / "kpis.json"
    kpis_path.write_text(json.dumps(result.kpis, indent=2, allow_nan=False), encoding="utf-8")

    facility_csv = racks_csv = events_csv = None
    if csv:
        facility_csv = directory / "facility.csv"
        racks_csv = directory / "racks.csv"
        events_csv = directory / "events.csv"
        result.facility.to_csv(facility_csv, index=False)
        result.racks.to_csv(racks_csv, index=False)
        result.events.to_csv(events_csv, index=False)

    viz_path = write_html(result, scenario, directory / "index.html", target_points=viz_points) if viz else None
    analysis_path = write_analysis(result, scenario, directory / "analysis.html") if analysis else None

    return Artifacts(
        directory=directory,
        viz=viz_path,
        analysis=analysis_path,
        facility_csv=facility_csv,
        racks_csv=racks_csv,
        events_csv=events_csv,
        kpis_json=kpis_path,
    )


def comparison_frame(results: list[RunResult]) -> pd.DataFrame:
    rows = []
    for result in results:
        row: dict[str, object] = {"scenario": result.scenario}
        row.update({key: result.kpis.get(key) for key, _ in COMPARE_KPIS})
        rows.append(row)
    return pd.DataFrame(rows)


def write_comparison(results: list[RunResult], out_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    frame = comparison_frame(results)
    csv_path = directory / "kpis.csv"
    frame.to_csv(csv_path, index=False)

    palette = [
        COLORS["it"],
        COLORS["a"],
        COLORS["b"],
        COLORS["gen"],
        COLORS["mech"],
        COLORS["batt"],
        COLORS["warn"],
        COLORS["bad"],
    ]
    figure = go.Figure()
    for index, (key, label) in enumerate(COMPARE_KPIS):
        figure.add_trace(
            go.Bar(
                name=label,
                x=frame["scenario"],
                y=[0.0 if value is None else value for value in frame[key]],
                marker_color=palette[index % len(palette)],
                # Each KPI has its own units, so they share an axis only to be
                # compared *within* a metric, across scenarios.
                visible=True if index < 3 else "legendonly",
            )
        )
    figure.update_layout(barmode="group", height=520, title="sim1 scenario comparison", **LAYOUT)
    figure.update_yaxes(type="log", title_text="value (log scale, mixed units)")

    html_path = directory / "compare.html"
    html_path.write_text(figure.to_html(include_plotlyjs="inline", full_html=True), encoding="utf-8")
    return html_path, csv_path
