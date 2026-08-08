import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html
from plotly.subplots import make_subplots

from app.simulations.sim0.rack import Rack
from app.simulations.sim0.scenarios import get_scenario

_RACK_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]


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


def _kpi_card(label: str, value: str) -> html.Div:
    return html.Div(
        [html.Div(label, style={"color": "#888"}), html.H3(value, style={"margin": 0})],
        style={"padding": "0.75rem 1.5rem", "border": "1px solid #ddd", "borderRadius": "8px"},
    )


def _combined_figure(df: pd.DataFrame, kpis: dict, scenario) -> go.Figure:
    """Everything on one shared x-axis, stacked: power, efficiency, per-rack draw,
    per-rack temp, cooling. One row is an input (demand, capacities) or output
    (delivered, temp, used) — stacking them with a shared x-axis means dragging a
    zoom box on any row zooms all of them together, so a cooling-capacity dip, the
    resulting power gap, and the temperature spike it causes all stay lined up on
    the same time axis instead of living in separate, independently-zoomable charts."""
    cols = ["demand_kw", "consumed_kw", "it_kw", "loss_kw", "liquid_kw", "air_kw"]
    facility = df.groupby("t")[cols].sum().reset_index()
    rack = Rack(name="reference")

    fig = make_subplots(
        rows=5,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.045,
        row_heights=[0.2, 0.2, 0.2, 0.2, 0.2],
        subplot_titles=(
            "Facility power: demand (input) vs electrical draw delivered (output)",
            "Power delivery: IT load (useful) vs PSU/VRM loss (output) — same draw as above, split",
            "Rack power draw (output), per rack",
            "Rack temperature (output), per rack",
            "Cooling: liquid vs air used (output), against capacity (input)",
        ),
    )

    fig.add_trace(
        go.Scatter(x=facility["t"], y=facility["demand_kw"], mode="lines", name="demand", line={"dash": "dot"}),
        row=1,
        col=1,
    )
    fig.add_trace(go.Scatter(x=facility["t"], y=facility["consumed_kw"], mode="lines", name="delivered"), row=1, col=1)
    fig.add_hline(y=scenario.power.capacity_kw, line_dash="dot", annotation_text="power capacity", row=1, col=1)

    # stacked area: it_kw + loss_kw always sums back to the same "delivered" line
    # above — this just shows how much of that draw is useful vs PSU/VRM loss.
    fig.add_trace(
        go.Scatter(
            x=facility["t"],
            y=facility["it_kw"],
            mode="lines",
            name="IT load (useful)",
            stackgroup="draw",
            line={"width": 0.5},
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=facility["t"],
            y=facility["loss_kw"],
            mode="lines",
            name="PSU/VRM loss",
            stackgroup="draw",
            line={"width": 0.5, "color": "#d62728"},
        ),
        row=2,
        col=1,
    )

    for i, (rack_name, group) in enumerate(df.groupby("rack")):
        color = _RACK_COLORS[i % len(_RACK_COLORS)]
        fig.add_trace(
            go.Scatter(x=group["t"], y=group["consumed_kw"], mode="lines", name=rack_name, line={"color": color}),
            row=3,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=group["t"],
                y=group["temp_c"],
                mode="lines",
                name=rack_name,
                line={"color": color},
                showlegend=False,
            ),
            row=4,
            col=1,
        )
    fig.add_hline(
        y=rack.throttle_temp_c, line_dash="dot", line_color="orange", annotation_text="Tj throttle", row=4, col=1
    )
    fig.add_hline(y=rack.shutdown_temp_c, line_dash="dot", line_color="red", annotation_text="shutdown", row=4, col=1)

    fig.add_trace(go.Scatter(x=facility["t"], y=facility["liquid_kw"], mode="lines", name="liquid used"), row=5, col=1)
    fig.add_trace(go.Scatter(x=facility["t"], y=facility["air_kw"], mode="lines", name="air used"), row=5, col=1)
    fig.add_hline(
        y=kpis["cooling_liquid_capacity_kw"], line_dash="dot", annotation_text="liquid capacity", row=5, col=1
    )
    fig.add_hline(y=kpis["cooling_air_capacity_kw"], line_dash="dot", annotation_text="air capacity", row=5, col=1)

    cooling = scenario.cooling
    if cooling.failure_start_s is not None:
        end_s = cooling.failure_end_s if cooling.failure_end_s is not None else cooling.failure_start_s
        fig.add_vrect(
            x0=cooling.failure_start_s,
            x1=end_s,
            fillcolor="red",
            opacity=0.10,
            line_width=0,
            annotation_text="cooling incident",
            annotation_position="top left",
            row="all",
            col="all",
        )

    fig.update_yaxes(title_text="kW", row=1, col=1)
    fig.update_yaxes(title_text="kW", row=2, col=1)
    fig.update_yaxes(title_text="kW", row=3, col=1)
    fig.update_yaxes(title_text="°C", row=4, col=1)
    fig.update_yaxes(title_text="kW", row=5, col=1)
    fig.update_xaxes(title_text="t, s", row=5, col=1)
    fig.update_layout(height=1350, margin={"t": 50}, legend={"tracegroupgap": 0})
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

    if isinstance(scenario.profile, list):
        assignments = ", ".join(f"rack-{i + 1}: {p}" for i, p in enumerate(scenario.profile))
        profile_line = f"Workload profile: {assignments} (each rack independently jittered)"
    else:
        profile_line = f"Workload profile: {scenario.profile} ({scenario.n_racks} racks, independent jitter per rack)"

    inputs = [
        profile_line,
        f"PowerInput: {scenario.power.capacity_kw:.0f} kW available (constant for the whole run)",
        (
            f"CoolingInput: {kpis['cooling_liquid_capacity_kw']:.0f} kW liquid + "
            f"{kpis['cooling_air_capacity_kw']:.0f} kW air (constant capacity)"
        ),
        f"Rack rating: {rack.nominal_kw:.0f} kW TDP / {rack.peak_kw:.0f} kW EDPp (peak) per rack, "
        f"{rack.liquid_capture_rate * 100:.0f}% liquid capture rate ({(1 - rack.liquid_capture_rate) * 100:.0f}% air), "
        f"{rack.psu_efficiency * 100:.0f}% PSU/VRM efficiency",
    ]

    outputs = [
        "Facility power: workload demand vs electrical draw actually delivered, against capacity",
        "Power delivery breakdown: IT load (useful) vs PSU/VRM loss, stacked to the same draw",
        "Rack power draw (consumed_kw) — per rack, over time",
        "Rack temperature (temp_c) — per rack, over time",
        "Facility cooling draw — liquid vs air used, against capacity",
        f"Energy: {kpis['energy_kwh']} kWh, delivery efficiency: {kpis['delivery_efficiency_pct']}%",
        f"Throttle events: {kpis['throttle_events']}, Shutdown events: {kpis['shutdown_events']}",
    ]

    assumptions = [
        "Power/cooling capacity can be scripted to drop (like this scenario's cooling incident), but there's no "
        "real failure-probability or repair-time behind it — it's a signal, not a physical object.",
        "Thermal model is simplified, not validated physics — read the shape of the curves, not the exact numbers.",
        f"Throttle at Tj {rack.throttle_temp_c:.0f}°C (cap to {rack.throttle_ratio * 100:.0f}% of peak), "
        f"emergency shutdown at {rack.shutdown_temp_c:.0f}°C, recovers below {rack.recovery_temp_c:.0f}°C.",
        "Consumed power (electrical draw) never exceeds the rack's rated peak, even if demand is higher.",
        f"PSU/VRM efficiency ({rack.psu_efficiency * 100:.0f}%) only splits draw into useful IT power vs "
        "conversion loss for reporting — both fully become heat either way, so it doesn't change the thermal "
        "calc. Not full facility PUE: cooling's own electrical draw and upstream UPS/transformer loss aren't "
        "tracked (see docs/sim_0_plan.md).",
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
    combined_fig = _combined_figure(display_df, kpis, scenario)

    app.layout = html.Div(
        [
            html.H2(f"Sim-0: {scenario_name}"),
            _model_panel(scenario_name, kpis),
            html.Div(
                [
                    _kpi_card("Energy", f"{kpis['energy_kwh']} kWh"),
                    _kpi_card("Delivery efficiency", f"{kpis['delivery_efficiency_pct']}%"),
                    _kpi_card("Throttle events", str(kpis["throttle_events"])),
                    _kpi_card("Shutdown events", str(kpis["shutdown_events"])),
                ],
                style={"display": "flex", "gap": "1rem", "marginBottom": "1.5rem"},
            ),
            dcc.Graph(figure=combined_fig),
        ],
        style={"fontFamily": "sans-serif", "padding": "1.5rem"},
    )
    return app
