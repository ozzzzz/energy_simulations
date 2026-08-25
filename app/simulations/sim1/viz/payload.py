"""Turn a run into the JSON the single-file visualization embeds.

The arithmetic that drives every decision here: a one-week run at dt=60 with
event refinement is ~14,000 ticks over ~110 columns. As row-of-objects JSON that
is roughly 25 MB, and column-oriented at full precision still ~9 MB. Both are
unusable in a file meant to open by double-click.

Four cuts, in order of payoff: column-oriented arrays (kills repeated key
names), shipping the ~35 primitive series instead of all 110 (losses, cumulative
costs and totals are derived in JS), per-series integer quantization, and
non-uniform downsampling that keeps event windows at fine resolution. Together
they land a week under a megabyte.

Per-series scaling is not optional: a single global scale of 0.1 would collapse
state-of-charge, which lives in [0, 1], into three distinct values.
"""

import json
import math
from typing import Any

import pandas as pd

from app.simulations.sim1.engine import RunResult
from app.simulations.sim1.resample import (
    aggregate_extreme,
    aggregate_series,
    aggregate_state,
    bucket_times,
    plan_buckets,
)
from app.simulations.sim1.scenarios import ScenarioConfig, build_facility, design_margins
from app.simulations.sim1.telemetry import FACILITY_SERIES, RACK_SERIES, RIBBON_COLUMNS


def _quantize(values: list[float | None], scale: float) -> dict[str, Any]:
    """Store a series as integers plus a scale, preserving gaps as nulls."""
    out: list[int | None] = []
    for value in values:
        if value is None or not math.isfinite(value):
            out.append(None)
        else:
            out.append(int(round(value / scale)))
    return {"scale": scale, "v": out}


def _state_series(labels: list[str]) -> dict[str, Any]:
    codes = sorted(set(labels))
    index = {label: i for i, label in enumerate(codes)}
    return {"codes": codes, "v": [index[label] for label in labels]}


def build_payload(result: RunResult, scenario: ScenarioConfig, target_points: int = 2500) -> dict[str, Any]:
    df = result.facility
    windows = tuple((event.t - 60.0, event.t + 600.0) for event in scenario.events)
    buckets = plan_buckets(df["t"].tolist(), df["dt"].tolist(), target_points, windows, window_step_s=2.0)
    times, _spans = bucket_times(df, buckets)

    series: dict[str, Any] = {}
    labels: dict[str, str] = {}
    for spec in FACILITY_SERIES:
        if spec.column not in df.columns:
            continue
        series[spec.column] = _quantize(aggregate_series(df, spec, buckets), spec.scale)
        labels[spec.column] = spec.label or spec.column
        companion = spec.companion_column
        if companion is not None:
            extreme = aggregate_extreme(df, spec.column, buckets, spec.companion or "max")
            series[companion] = _quantize(extreme, spec.scale)

    states = {column: _state_series(aggregate_state(df, column, buckets)) for column in RIBBON_COLUMNS}

    racks: dict[str, Any] = {}
    if not result.racks.empty:
        for name, group in result.racks.groupby("rack", sort=True):
            group = group.sort_values("t").reset_index(drop=True)
            rack_buckets = plan_buckets(
                group["t"].tolist(), group["dt"].tolist(), target_points, windows, window_step_s=2.0
            )
            entry: dict[str, Any] = {"state": _state_series(aggregate_state(group, "state", rack_buckets))}
            for spec in RACK_SERIES:
                entry[spec.column] = _quantize(aggregate_series(group, spec, rack_buckets), spec.scale)
                companion = spec.companion_column
                if companion is not None:
                    entry[companion] = _quantize(
                        aggregate_extreme(group, spec.column, rack_buckets, spec.companion or "max"), spec.scale
                    )
            racks[str(name)] = entry

    site = scenario.site
    reference = design_margins(site)
    racks_meta = build_facility(scenario).racks
    reference_rack = result.racks.iloc[0] if not result.racks.empty else None

    return {
        "scenario": result.scenario,
        "description": result.description,
        "duration_s": result.duration_s,
        "dt_s": result.dt_s,
        "dt_fine_s": result.dt_fine_s,
        "points": len(buckets),
        "ticks": int(len(df)),
        "t": [round(value, 3) for value in times],
        "series": series,
        "labels": labels,
        "states": states,
        "racks": racks,
        "rack_names": sorted(racks),
        "events": _events(result.events),
        "kpis": _clean(result.kpis),
        "meta": {
            "ups_rating_kw": site.ups_rating_kw,
            "transformer_capacity_kw": site.transformer_capacity_kw,
            "pdu_rating_kw": site.pdu_rating_kw,
            "generator_rating_kw": site.generator_rating_kw,
            "fuel_capacity_l": site.fuel_capacity_l,
            "battery_capacity_kwh": site.battery_capacity_kwh,
            "chiller_capacity_kw": site.chiller_capacity_kw,
            "crah_capacity_kw": site.crah_capacity_kw,
            "cord_limit_kw": site.cord_limit_kw,
            "it_nominal_kw": site.it_nominal_kw,
            "it_peak_kw": site.it_peak_kw,
            "n_racks": site.n_racks,
            "start_hour": site.start_hour,
            "peak_rps": site.peak_rps,
            "capacity_rps": reference["capacity_rps"],
            "slo_latency_s": site.slo_latency_s,
            "tokens_per_request": site.tokens_per_request,
            "interactive_racks": int(site.interactive_racks),
            "rack_segments": {rack.name: rack.segment.value for rack in racks_meta},
            "throttle_c": 85.0,
            "shutdown_c": 95.0,
            "facility_nominal_kw": round(reference["facility_nominal_kw"], 1),
            "pue_nominal": round(reference["pue_nominal"], 3),
            "rack_sample": None if reference_rack is None else str(reference_rack["rack"]),
        },
    }


def _events(events: pd.DataFrame) -> list[dict[str, Any]]:
    """Never downsampled: the timeline is what makes a failure run readable."""
    if events.empty:
        return []
    return [
        {
            "t": float(record["t"]),
            "component": str(record["component"]),
            "kind": str(record["kind"]),
            "from": str(record["from_state"]),
            "to": str(record["to_state"]),
            "detail": str(record["detail"]),
        }
        for record in events.to_dict("records")
    ]


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, float) and not math.isfinite(value):
            out[key] = None
        else:
            out[key] = value
    return out


def dumps(payload: dict[str, Any]) -> str:
    """Serialize for embedding in a ``<script type="application/json">`` block.

    ``allow_nan=False`` is the guard that matters: ``json.dumps`` will otherwise
    happily emit bare ``Infinity`` and ``NaN``, which ``JSON.parse`` rejects
    outright — the page would load blank with only a console error to show for
    it. ``<`` is escaped so no value can close the script element early.
    """
    text = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
