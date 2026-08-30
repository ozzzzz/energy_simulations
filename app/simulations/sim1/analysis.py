"""Plotly analysis figures for a run, written as one self-contained HTML file.

One file with subplots rather than nine separate ones, because ``plotly.js``
inlines at roughly 3.5 MB and paying that once is fine while paying it nine
times is not. Inline rather than a CDN reference so the output survives being
emailed around, and because ``write_image`` would need ``kaleido``, which is not
a dependency here.
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.simulations.sim1.kpis import energy_kwh
from app.simulations.sim1.models import RunResult, ScenarioConfig
from app.simulations.sim1.resample import thin

COLORS = {
    "it": "#38d39f",
    "mech": "#35c9d8",
    "loss": "#8892a4",
    "a": "#4aa3ff",
    "b": "#b48cff",
    "gen": "#f0932b",
    "batt": "#f5d76e",
    "heat": "#ff6b6b",
    "air": "#d3c15c",
    "warn": "#ffb020",
    "bad": "#ff5c5c",
    "ink": "#e6edf3",
    "dim": "#93a1b1",
}

LAYOUT = {
    "template": "plotly_dark",
    "paper_bgcolor": "#0e1116",
    "plot_bgcolor": "#12171f",
    "font": {"color": COLORS["ink"], "size": 11},
    "margin": {"l": 64, "r": 24, "t": 48, "b": 40},
    "hovermode": "x unified",
}

_ROWS = (
    "User requests (rps)",
    "Request queue and latency",
    "Facility power",
    "Side A vs side B",
    "Battery and autonomy",
    "Generator and fuel",
    "Thermal",
    "Heat generated vs rejected",
    "PUE",
    "Unserved IT demand",
)


def _line(df: pd.DataFrame, column: str, name: str, color: str, dash: str | None = None) -> go.Scatter:
    return go.Scatter(
        x=df["t"] / 3600.0,
        y=df[column],
        name=name,
        mode="lines",
        line={"color": color, "width": 1.6, "dash": dash} if dash else {"color": color, "width": 1.6},
    )


def _hline(fig: go.Figure, row: int, value: float, label: str, color: str) -> None:
    # row/col are ints at runtime; the bundled plotly stubs declare them as str,
    # and passing the str the stub asks for makes plotly raise.
    fig.add_hline(
        y=value,
        row=row,  # type: ignore[arg-type]
        col=1,  # type: ignore[arg-type]
        line={"color": color, "width": 1, "dash": "dot"},
        annotation={"text": label, "font": {"size": 9, "color": color}},
        annotation_position="top left",
    )


def timeline_figure(result: RunResult, scenario: ScenarioConfig) -> go.Figure:
    site = scenario.site
    df = thin(result.facility, 4000, tuple((e.t - 60.0, e.t + 900.0) for e in scenario.events))
    fig = make_subplots(rows=len(_ROWS), cols=1, shared_xaxes=True, subplot_titles=_ROWS, vertical_spacing=0.035)

    hours = df["t"] / 3600.0

    # Users first: this is where the load comes from, and every row below it is a
    # consequence of this one.
    fig.add_trace(_line(df, "user_offered_rps", "Offered", COLORS["ink"]), row=1, col=1)
    fig.add_trace(_line(df, "user_served_rps", "Served", COLORS["it"]), row=1, col=1)
    fig.add_trace(_line(df, "user_dropped_rps", "Dropped", COLORS["bad"]), row=1, col=1)
    fig.add_trace(_line(df, "user_capacity_rps", "Capacity", COLORS["warn"], dash="dot"), row=1, col=1)

    fig.add_trace(_line(df, "user_queued_requests", "Queued requests", COLORS["batt"]), row=2, col=1)
    fig.add_trace(_line(df, "user_queue_latency_s", "Queue latency (s)", COLORS["air"]), row=2, col=1)
    _hline(fig, 2, site.slo_latency_s, "latency budget", COLORS["bad"])

    stack = {"stackgroup": "power", "line": {"width": 0.8}}
    for column, name, fill in (
        ("it_drawn_kw", "IT", "rgba(56,211,159,0.40)"),
        ("mech_kw", "Cooling", "rgba(53,201,216,0.40)"),
        ("loss_kw", "Conversion loss", "rgba(136,146,164,0.40)"),
    ):
        fig.add_trace(go.Scatter(x=hours, y=df[column], name=name, fillcolor=fill, **stack), row=3, col=1)

    fig.add_trace(_line(df, "a_delivered_kw", "Side A", COLORS["a"]), row=4, col=1)
    fig.add_trace(_line(df, "b_delivered_kw", "Side B", COLORS["b"]), row=4, col=1)
    _hline(fig, 4, site.ups_rating_kw, "UPS nameplate", COLORS["warn"])

    fig.add_trace(_line(df, "a_batt_soc", "SoC A", COLORS["a"]), row=5, col=1)
    fig.add_trace(_line(df, "b_batt_soc", "SoC B", COLORS["b"]), row=5, col=1)
    _hline(fig, 5, 0.05, "cutoff", COLORS["bad"])

    fig.add_trace(_line(df, "gen_output_kw", "Genset kW", COLORS["gen"]), row=6, col=1)
    fig.add_trace(_line(df, "gen_fuel_l", "Fuel L", COLORS["batt"], dash="dot"), row=6, col=1)
    _hline(fig, 6, site.generator_rating_kw, "genset rating", COLORS["warn"])

    fig.add_trace(_line(df, "rack_temp_max_c", "Hottest rack", COLORS["heat"]), row=7, col=1)
    fig.add_trace(_line(df, "cool_loop_supply_c", "Loop supply", COLORS["mech"]), row=7, col=1)
    fig.add_trace(_line(df, "cool_loop_return_c", "Loop return", COLORS["a"], dash="dot"), row=7, col=1)
    fig.add_trace(_line(df, "cool_crah_room_c", "Room air", COLORS["dim"]), row=7, col=1)
    _hline(fig, 7, 85.0, "throttle", COLORS["warn"])
    _hline(fig, 7, 95.0, "shutdown", COLORS["bad"])

    generated = df["cool_liquid_heat_kw"] + df["cool_air_heat_kw"]
    rejected = df["cool_rejected_liquid_kw"] + df["cool_rejected_air_kw"]
    fig.add_trace(
        go.Scatter(
            x=df["t"] / 3600.0, y=generated, name="Heat generated", line={"color": COLORS["heat"], "width": 1.6}
        ),
        row=6,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["t"] / 3600.0, y=rejected, name="Heat rejected", line={"color": COLORS["mech"], "width": 1.6}),
        row=6,
        col=1,
    )
    _hline(fig, 8, site.chiller_capacity_kw, "chiller capacity", COLORS["warn"])

    fig.add_trace(_line(df, "pue", "PUE", COLORS["it"]), row=9, col=1)
    fig.add_trace(_line(df, "it_unserved_kw", "Unserved", COLORS["bad"]), row=10, col=1)

    for event in scenario.events:
        fig.add_vline(x=event.t / 3600.0, line={"color": COLORS["warn"], "width": 1, "dash": "dash"})

    fig.update_layout(height=200 * len(_ROWS), showlegend=True, title=f"sim1 · {result.scenario}", **LAYOUT)
    fig.update_xaxes(title_text="hours", row=len(_ROWS), col=1)
    for row, unit in enumerate(["rps", "requests / s", "kW", "kW", "SoC", "kW / L", "°C", "kW", "", "kW"], start=1):
        fig.update_yaxes(title_text=unit, row=row, col=1)
    return fig


def sankey_figure(result: RunResult) -> go.Figure:
    """The one figure that shows every transformation at once.

    Because every node balances by construction, it doubles as a visual
    conservation check: any node whose inflow and outflow disagree is a bug.
    """
    df = result.facility
    grid_a = energy_kwh(df, "a_grid_kw")
    grid_b = energy_kwh(df, "b_grid_kw")
    gen = energy_kwh(df, "gen_output_kw")
    batt = energy_kwh(df, "a_batt_out_kw") + energy_kwh(df, "b_batt_out_kw")
    loss = energy_kwh(df, "loss_kw")
    mech = energy_kwh(df, "mech_kw")
    it = energy_kwh(df, "it_drawn_kw")
    charge = energy_kwh(df, "charge_kw")
    liquid = energy_kwh(df, "cool_rejected_liquid_kw")
    air = energy_kwh(df, "cool_rejected_air_kw")
    stored = max(0.0, (it + mech) - (liquid + air))

    labels = [
        "Utility A",
        "Utility B",
        "Diesel",
        "Battery discharge",
        "Site bus",
        "Conversion loss",
        "Battery charge",
        "IT load",
        "Cooling plant",
        "Heat",
        "Rejected (liquid)",
        "Rejected (air)",
        "Stored in loop / fabric",
    ]
    links = [
        (0, 4, grid_a),
        (1, 4, grid_b),
        (2, 4, gen),
        (3, 4, batt),
        (4, 5, loss),
        (4, 6, charge),
        (4, 7, it),
        (4, 8, mech),
        (7, 9, it),
        (8, 9, mech),
        (9, 10, liquid),
        (9, 11, air),
        (9, 12, stored),
    ]
    colors = [
        COLORS["a"],
        COLORS["b"],
        COLORS["gen"],
        COLORS["batt"],
        COLORS["ink"],
        COLORS["loss"],
        COLORS["batt"],
        COLORS["it"],
        COLORS["mech"],
        COLORS["heat"],
        COLORS["mech"],
        COLORS["air"],
        COLORS["warn"],
    ]

    fig = go.Figure(
        go.Sankey(
            node={"label": labels, "color": colors, "pad": 16, "thickness": 16},
            link={
                "source": [s for s, _, _ in links],
                "target": [t for _, t, _ in links],
                "value": [max(0.0, round(v, 3)) for _, _, v in links],
                "color": "rgba(146,161,177,0.28)",
            },
        )
    )
    fig.update_layout(title="Energy over the whole run (kWh)", height=520, **LAYOUT)
    return fig


def requests_figure(result: RunResult) -> go.Figure:
    """Where the offered requests went, over the whole run."""
    served = float(result.kpis.get("requests_served") or 0.0)
    dropped = float(result.kpis.get("requests_dropped") or 0.0)
    if served + dropped <= 0.0:
        return go.Figure(layout={"title": "No user traffic in this scenario", "height": 220, **LAYOUT})

    fig = go.Figure(
        go.Bar(
            x=[served, dropped],
            y=["Served", "Dropped"],
            orientation="h",
            marker_color=[COLORS["it"], COLORS["bad"]],
            text=[f"{served:,.0f}", f"{dropped:,.0f}"],
            textposition="auto",
        )
    )
    drop_pct = float(result.kpis.get("request_drop_pct") or 0.0)
    fig.update_layout(title=f"User requests over the run — {drop_pct:.2f} % dropped", height=260, **LAYOUT)
    return fig


def cost_figure(result: RunResult) -> go.Figure:
    k = result.kpis
    parts = [
        ("Grid energy", float(k.get("energy_cost_usd") or 0.0)),
        ("Diesel", float(k.get("diesel_cost_usd") or 0.0)),
        ("Unserved compute", float(k.get("unserved_cost_usd") or 0.0)),
        ("Downtime", float(k.get("downtime_cost_usd") or 0.0)),
    ]
    fig = go.Figure(
        go.Waterfall(
            x=[name for name, _ in parts] + ["Total"],
            y=[value for _, value in parts] + [0.0],
            measure=["relative"] * len(parts) + ["total"],
            connector={"line": {"color": COLORS["dim"]}},
            increasing={"marker": {"color": COLORS["bad"]}},
            totals={"marker": {"color": COLORS["it"]}},
        )
    )
    fig.update_layout(title="Cost of the run (USD)", height=380, **LAYOUT)
    return fig


_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sim1 analysis — {scenario}</title>
<style>
 body {{ margin:0; padding:18px; background:#0e1116; color:#e6edf3;
        font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 p.lede {{ color:#93a1b1; max-width:900px; margin:0 0 18px; }}
 .fig {{ overflow-x:auto; margin-bottom:18px; }}
</style></head><body>
<h1>sim1 · {scenario}</h1>
<p class="lede">{description}</p>
{figures}
</body></html>
"""


def build_analysis_html(result: RunResult, scenario: ScenarioConfig) -> str:
    figures = [
        timeline_figure(result, scenario),
        requests_figure(result),
        sankey_figure(result),
        cost_figure(result),
    ]
    chunks = []
    for index, figure in enumerate(figures):
        # plotly.js goes in once, with the first figure only.
        html = figure.to_html(include_plotlyjs="inline" if index == 0 else False, full_html=False)
        chunks.append(f'<div class="fig">{html}</div>')
    return _SHELL.format(scenario=result.scenario, description=result.description, figures="\n".join(chunks))


def write_analysis(result: RunResult, scenario: ScenarioConfig, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_analysis_html(result, scenario), encoding="utf-8")
    return destination
