import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html

from app.simulations.sim0.rack import Rack
from app.simulations.sim0.scenarios import get_scenario


def _resample_for_display(df: pd.DataFrame, target_points: int = 1500) -> pd.DataFrame:
    """Bucket-average long runs down to ~target_points on the time axis.

    A week at a 1-minute tick is 10k+ points per rack; plotted raw, fast
    profile oscillations (checkpoint dips, request spikes) alias into a dense
    scribble once compressed to chart width. Averaging within each bucket
    keeps the trend legible without touching the underlying simulation data."""
    ticks = df["t"].nunique()
    if ticks <= target_points:
        return df

    span = df["t"].max() - df["t"].min()
    bucket_width = span / target_points
    bucketed = df.copy()
    bucketed["t"] = (bucketed["t"] // bucket_width) * bucket_width
    return bucketed.groupby(["rack", "t"], as_index=False).mean(numeric_only=True)


def _mark_incident(fig: go.Figure, scenario) -> None:
    cooling = scenario.cooling
    if cooling.failure_start_s is None:
        return
    end_s = cooling.failure_end_s if cooling.failure_end_s is not None else cooling.failure_start_s
    fig.add_vrect(
        x0=cooling.failure_start_s,
        x1=end_s,
        fillcolor="red",
        opacity=0.12,
        line_width=0,
        annotation_text="cooling incident",
        annotation_position="top left",
    )


def _line_figure(df: pd.DataFrame, y: str, title: str, y_title: str, scenario) -> go.Figure:
    fig = go.Figure()
    for rack_name, group in df.groupby("rack"):
        fig.add_trace(go.Scatter(x=group["t"], y=group[y], mode="lines", name=rack_name))
    _mark_incident(fig, scenario)
    fig.update_layout(title=title, xaxis_title="t, s", yaxis_title=y_title, margin={"t": 40})
    return fig


def _kpi_card(label: str, value: str) -> html.Div:
    return html.Div(
        [html.Div(label, style={"color": "#888"}), html.H3(value, style={"margin": 0})],
        style={"padding": "0.75rem 1.5rem", "border": "1px solid #ddd", "borderRadius": "8px"},
    )


def _power_figure(df: pd.DataFrame, scenario) -> go.Figure:
    facility = df.groupby("t")[["demand_kw", "consumed_kw"]].sum().reset_index()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=facility["t"], y=facility["demand_kw"], mode="lines", name="demand (workload)"))
    fig.add_trace(go.Scatter(x=facility["t"], y=facility["consumed_kw"], mode="lines", name="delivered"))
    fig.add_hline(y=scenario.power.capacity_kw, line_dash="dot", annotation_text="power capacity (nameplate)")
    _mark_incident(fig, scenario)
    fig.update_layout(
        title="Facility power: demand vs delivered", xaxis_title="t, s", yaxis_title="kW", margin={"t": 40}
    )
    return fig


def _cooling_figure(df: pd.DataFrame, kpis: dict, scenario) -> go.Figure:
    facility = df.groupby("t")[["liquid_kw", "air_kw"]].sum().reset_index()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=facility["t"], y=facility["liquid_kw"], mode="lines", name="liquid used"))
    fig.add_trace(go.Scatter(x=facility["t"], y=facility["air_kw"], mode="lines", name="air used"))
    fig.add_hline(y=kpis["cooling_liquid_capacity_kw"], line_dash="dot", annotation_text="liquid capacity (nameplate)")
    fig.add_hline(y=kpis["cooling_air_capacity_kw"], line_dash="dot", annotation_text="air capacity (nameplate)")
    _mark_incident(fig, scenario)
    fig.update_layout(
        title="Cooling: liquid vs air (facility total)", xaxis_title="t, s", yaxis_title="kW", margin={"t": 40}
    )
    return fig


def _info_column(title: str, items: list[str]) -> html.Div:
    return html.Div(
        [
            html.Div(title, style={"fontWeight": "bold", "marginBottom": "0.4rem"}),
            html.Ul([html.Li(item) for item in items], style={"margin": 0, "paddingLeft": "1.1rem"}),
        ],
        style={"flex": 1, "minWidth": "260px"},
    )


def _model_panel(scenario_name: str, kpis: dict) -> html.Div:
    scenario = get_scenario(scenario_name)
    rack = Rack(name="reference")

    inputs = [
        f"Workload profile: {scenario.profile} ({scenario.n_racks} racks, independent jitter per rack)",
        f"PowerInput: {scenario.power.capacity_kw:.0f} kW available (constant for the whole run)",
        (
            f"CoolingInput: {kpis['cooling_liquid_capacity_kw']:.0f} kW liquid + "
            f"{kpis['cooling_air_capacity_kw']:.0f} kW air (constant capacity)"
        ),
        f"Rack rating: {rack.nominal_kw:.0f} kW nominal / {rack.peak_kw:.0f} kW peak per rack, "
        f"{rack.liquid_heat_fraction * 100:.0f}%/{(1 - rack.liquid_heat_fraction) * 100:.0f}% liquid/air heat split",
    ]

    outputs = [
        "Facility power: workload demand vs what was actually delivered, against capacity",
        "Rack power draw (consumed_kw) — per rack, over time",
        "Rack temperature (temp_c) — per rack, over time",
        "Facility cooling draw — liquid vs air used, against capacity",
        f"Uptime: {kpis['uptime_pct']}%, Energy: {kpis['energy_kwh']} kWh",
        f"Throttle events: {kpis['throttle_events']}, Shutdown events: {kpis['shutdown_events']}",
    ]

    assumptions = [
        "Power/cooling are input signals, not modeled objects — a scripted capacity drop (like this scenario's "
        "cooling incident) is possible, but there's no failure-probability or repair-process model behind it "
        "(that's sim_1).",
        "Thermal model is a simplified linear tank, not real physics — a digital twin of the structure, not the world.",
        f"Throttle at {rack.throttle_temp_c:.0f}°C (cap to {rack.throttle_ratio * 100:.0f}% of peak), "
        f"emergency shutdown at {rack.shutdown_temp_c:.0f}°C, recovers below {rack.recovery_temp_c:.0f}°C.",
        "No event bus: state changes are just logged rows each tick (see docs/sim_0_plan.md).",
        "Consumed power is hard-capped at the rack's rated peak regardless of demand or thermal state.",
    ]

    return html.Div(
        [
            _info_column("Inputs", inputs),
            _info_column("Outputs", outputs),
            _info_column("Assumptions", assumptions),
        ],
        style={
            "display": "flex",
            "gap": "2rem",
            "flexWrap": "wrap",
            "marginBottom": "1.5rem",
            "padding": "1rem",
            "border": "1px solid #ddd",
            "borderRadius": "8px",
            "fontSize": "0.9rem",
        },
    )


def build_app(df: pd.DataFrame, kpis: dict, scenario_name: str) -> Dash:
    app = Dash(__name__)

    scenario = get_scenario(scenario_name)
    display_df = _resample_for_display(df)
    demand_fig = _power_figure(display_df, scenario)
    power_fig = _line_figure(display_df, "consumed_kw", "Rack power draw", "kW", scenario)
    temp_fig = _line_figure(display_df, "temp_c", "Rack temperature", "°C", scenario)
    cooling_fig = _cooling_figure(display_df, kpis, scenario)

    app.layout = html.Div(
        [
            html.H2(f"Sim-0: {scenario_name}"),
            _model_panel(scenario_name, kpis),
            html.Div(
                [
                    _kpi_card("Uptime", f"{kpis['uptime_pct']}%"),
                    _kpi_card("Energy", f"{kpis['energy_kwh']} kWh"),
                    _kpi_card("Throttle events", str(kpis["throttle_events"])),
                    _kpi_card("Shutdown events", str(kpis["shutdown_events"])),
                ],
                style={"display": "flex", "gap": "1rem", "marginBottom": "1.5rem"},
            ),
            dcc.Graph(figure=demand_fig),
            dcc.Graph(figure=power_fig),
            dcc.Graph(figure=temp_fig),
            dcc.Graph(figure=cooling_fig),
        ],
        style={"fontFamily": "sans-serif", "padding": "1.5rem"},
    )
    return app
