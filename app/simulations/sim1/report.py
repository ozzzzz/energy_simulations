"""Turn a run into files on disk.

Kept out of the CLI so the command layer stays declarative and this stays
testable without a ``CliRunner``.
"""

import json
from pathlib import Path

from app.simulations.sim1.analysis import write_analysis
from app.simulations.sim1.models import Artifacts, RunResult, ScenarioConfig
from app.simulations.sim1.viz.builder import write_html


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
