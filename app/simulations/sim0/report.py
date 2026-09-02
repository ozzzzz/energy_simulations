"""Static HTML report for a sim-0 run.

A run's four figures written to one self-contained file, so `sim0-all` leaves
something on disk to look at — the sim-1 side has had `analysis.html` from the
start.

Whole run, plus the incident window and its complement; plotly.js is inlined
once, so the file opens with no network at all.
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from app.simulations.sim0.figures import (
    context_figure,
    cooling_figure,
    detail_figure,
    flow_totals,
    resample_for_display,
    sankey_figure,
    select,
    window_options,
)
from app.simulations.sim0.scenarios import get_scenario

_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sim0 — {scenario}</title>
<style>
 body {{ margin:0; padding:20px; background:#fafafa; color:#222;
        font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:1px; color:#666;
       margin:28px 0 8px; padding-top:14px; border-top:1px solid #ddd; }}
 p.lede {{ color:#666; max-width:900px; margin:0 0 18px; }}
 .kpis {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:8px; }}
 .kpi {{ border:1px solid #ddd; border-radius:8px; padding:7px 12px;
         background:#fff; min-width:120px; }}
 .kpi .k {{ display:block; font-size:10px; text-transform:uppercase;
            letter-spacing:0.6px; color:#888; }}
 .kpi .v {{ display:block; font-size:17px; font-variant-numeric:tabular-nums; }}
 .fig {{ overflow-x:auto; }}
</style></head><body>
<h1>sim0 · {scenario}</h1>
<p class="lede">{lede}</p>
<div class="kpis">{kpis}</div>
{sections}
</body></html>
"""

_KPI_LABELS: tuple[tuple[str, str, str], ...] = (
    ("uptime_pct", "Uptime", "%"),
    ("energy_kwh", "Energy", "kWh"),
    ("delivery_efficiency_pct", "Delivery efficiency", "%"),
    ("throttle_events", "Throttle events", ""),
    ("shutdown_events", "Shutdown events", ""),
    ("cooling_liquid_capacity_kw", "Liquid capacity", "kW"),
    ("cooling_air_capacity_kw", "Air capacity", "kW"),
)


def _kpi_html(kpis: dict) -> str:
    cards = []
    for key, label, unit in _KPI_LABELS:
        if key not in kpis:
            continue
        value = kpis[key]
        shown = f"{value:g}" if isinstance(value, int | float) else str(value)
        suffix = f" {unit}" if unit else ""
        cards.append(f'<div class="kpi"><span class="k">{label}</span><span class="v">{shown}{suffix}</span></div>')
    return "".join(cards)


def _figures_for(df: pd.DataFrame, kpis: dict, scenario, window: str) -> list[go.Figure]:
    """The four figures for one window.

    The Sankey uses full-resolution data because it integrates totals; the time
    series are bucket-averaged first.
    """
    windowed = select(df, window, scenario)
    display = resample_for_display(windowed)
    return [
        sankey_figure(flow_totals(windowed), "Where every kW of draw goes"),
        context_figure(display, scenario, window),
        cooling_figure(display, kpis, scenario, window),
        detail_figure(display, scenario, window),
    ]


def build_report_html(df: pd.DataFrame, kpis: dict, scenario_name: str) -> str:
    scenario = get_scenario(scenario_name)
    windows = window_options(scenario)

    sections: list[str] = []
    first = True
    for option in windows:
        window = option["value"]
        if len(windows) > 1:
            sections.append(f"<h2>{option['label']}</h2>")
        for figure in _figures_for(df, kpis, scenario, window):
            # plotly.js goes in once, with the very first figure only.
            html = figure.to_html(include_plotlyjs="inline" if first else False, full_html=False)
            sections.append(f'<div class="fig">{html}</div>')
            first = False

    profile = scenario.profile if isinstance(scenario.profile, str) else ", ".join(scenario.profile)
    lede = (
        f"{scenario.n_racks} racks on the <b>{profile}</b> profile. "
        "Power and cooling are input signals here — sim-1 models the chain that produces them."
    )
    return _SHELL.format(scenario=scenario_name, lede=lede, kpis=_kpi_html(kpis), sections="\n".join(sections))


def write_report(df: pd.DataFrame, kpis: dict, scenario_name: str, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_report_html(df, kpis, scenario_name), encoding="utf-8")
    return destination
