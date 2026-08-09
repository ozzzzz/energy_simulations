import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html
from plotly.subplots import make_subplots

from app.simulations.sim0.rack import Rack
from app.simulations.sim0.scenarios import get_scenario

_RACK_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]

# liquid/air must stay visually distinct wherever they appear (Sankey nodes, cooling
# chart) — a previous teal-vs-pale-teal pairing here was nearly indistinguishable.
_LIQUID_COLOR = "#17becf"
_AIR_COLOR = "#bcbd22"

_NODE_LABELS = [
    "Workload demand",  # 0 input
    "Electrical draw",  # 1
    "Unmet demand",  # 2 (curtailed by power/thermal limits)
    "IT compute (useful)",  # 3
    "PSU/VRM loss",  # 4
    "Total heat generated",  # 5
    "Removed via liquid",  # 6 output
    "Removed via air",  # 7 output
]
_NODE_COLORS = ["#7f7f7f", "#1f77b4", "#d62728", "#2ca02c", "#e07b39", "#8c564b", _LIQUID_COLOR, _AIR_COLOR]


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


def _window_options(scenario) -> list[dict]:
    options = [{"label": "Whole run", "value": "whole"}]
    if scenario.cooling.failure_start_s is not None:
        options += [
            {"label": "During cooling incident", "value": "incident"},
            {"label": "Outside incident (normal)", "value": "normal"},
        ]
    return options


def _window_bounds(scenario, window: str) -> tuple[float | None, float | None]:
    cooling = scenario.cooling
    if window == "incident" and cooling.failure_start_s is not None:
        end_s = cooling.failure_end_s if cooling.failure_end_s is not None else cooling.failure_start_s
        return cooling.failure_start_s, end_s
    return None, None


def _select(df: pd.DataFrame, window: str, scenario) -> pd.DataFrame:
    if window == "whole":
        return df
    start_s, end_s = _window_bounds(scenario, "incident")
    if start_s is None:
        return df
    in_incident = (df["t"] >= start_s) & (df["t"] < end_s)
    return df[in_incident] if window == "incident" else df[~in_incident]


def _flow_totals(df: pd.DataFrame) -> dict:
    consumed = df["consumed_kw"].sum()
    unmet = (df["demand_kw"] - df["consumed_kw"]).clip(lower=0).sum()
    it = df["it_kw"].sum()
    loss = df["loss_kw"].sum()
    liquid = df["liquid_kw"].sum()
    air = df["air_kw"].sum()
    return {"consumed": consumed, "unmet": unmet, "it": it, "loss": loss, "liquid": liquid, "air": air}


def _sankey_figure(totals: dict, title: str) -> go.Figure:
    """The whole energy story as one diagram: demand splits into what got delivered
    vs curtailed; delivered draw splits into useful compute vs PSU/VRM loss; both
    fully become heat (that's the point — efficiency doesn't erase heat, it just
    relocates where in the chain it appears); heat splits into liquid/air. Every
    node's inflow equals its outflow — nothing is hand-waved."""
    values = [
        totals["consumed"],
        totals["unmet"],
        totals["it"],
        totals["loss"],
        totals["it"],
        totals["loss"],
        totals["liquid"],
        totals["air"],
    ]
    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            node={
                "label": _NODE_LABELS,
                "color": _NODE_COLORS,
                "pad": 20,
                "thickness": 18,
            },
            link={
                "source": [0, 0, 1, 1, 3, 4, 5, 5],
                "target": [1, 2, 3, 4, 5, 5, 6, 7],
                "value": [max(v, 1e-9) for v in values],
                "color": [
                    "rgba(31,119,180,0.35)",
                    "rgba(214,39,40,0.35)",
                    "rgba(44,160,44,0.35)",
                    "rgba(224,123,57,0.35)",
                    "rgba(44,160,44,0.35)",
                    "rgba(224,123,57,0.35)",
                    "rgba(23,190,207,0.35)",
                    "rgba(188,189,34,0.35)",
                ],
            },
        )
    )
    fig.update_layout(title=title, margin={"t": 60, "l": 10, "r": 10, "b": 10}, height=460)
    return fig


def _context_figure(df: pd.DataFrame, scenario, window: str) -> go.Figure:
    facility = df.groupby("t")[["demand_kw", "consumed_kw"]].sum().reset_index()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=facility["t"], y=facility["demand_kw"], mode="lines", name="demand", line={"dash": "dot"})
    )
    fig.add_trace(go.Scatter(x=facility["t"], y=facility["consumed_kw"], mode="lines", name="delivered"))

    start_s, end_s = _window_bounds(scenario, window)
    if start_s is not None:
        fig.add_vrect(x0=start_s, x1=end_s, fillcolor="blue", opacity=0.12, line_width=0)

    fig.update_layout(
        title="Where this window sits in the run (facility power, for reference)",
        xaxis_title="t, s",
        yaxis_title="kW",
        height=260,
        margin={"t": 40},
    )
    return fig


def _cooling_figure(df: pd.DataFrame, kpis: dict, scenario, window: str) -> go.Figure:
    """Cooling isn't one channel: liquid (direct-to-chip) carries most of the heat,
    air carries the rest, at a fixed 90/10 split. This is heat REMOVED (an output),
    plotted against each channel's nameplate capacity (a fixed input) — separate
    from the Sankey's aggregate totals, this shows how that split moves over time,
    e.g. whether either channel is running close to its own limit."""
    facility = df.groupby("t")[["liquid_kw", "air_kw"]].sum().reset_index()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=facility["t"],
            y=facility["liquid_kw"],
            mode="lines",
            name="liquid removed",
            line={"color": _LIQUID_COLOR},
        )
    )
    fig.add_trace(
        go.Scatter(x=facility["t"], y=facility["air_kw"], mode="lines", name="air removed", line={"color": _AIR_COLOR})
    )
    fig.add_hline(
        y=kpis["cooling_liquid_capacity_kw"],
        line_dash="dot",
        line_color=_LIQUID_COLOR,
        annotation_text="liquid capacity",
    )
    fig.add_hline(
        y=kpis["cooling_air_capacity_kw"], line_dash="dot", line_color=_AIR_COLOR, annotation_text="air capacity"
    )

    start_s, end_s = _window_bounds(scenario, window)
    if start_s is not None:
        fig.add_vrect(x0=start_s, x1=end_s, fillcolor="blue", opacity=0.12, line_width=0)

    fig.update_layout(
        title="Cooling by channel: liquid vs air (facility total), against nameplate capacity",
        xaxis_title="t, s",
        yaxis_title="kW",
        height=300,
        margin={"t": 40},
    )
    return fig


def _detail_figure(df: pd.DataFrame, scenario, window: str) -> go.Figure:
    """Drill-down per rack: the Sankey and context chart above are facility totals,
    hiding whether one rack is behaving very differently from the others (e.g. the
    'mixed' scenario deliberately runs rack 1 on a different workload). Dashed =
    input (demand), solid = output (draw / temperature) — same convention per rack."""
    rack = Rack(name="reference")

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.5, 0.5],
        subplot_titles=(
            "Per rack: demand (input, dashed) vs electrical draw (output, solid)",
            "Per rack: temperature (output)",
        ),
    )

    for i, (rack_name, group) in enumerate(df.groupby("rack")):
        color = _RACK_COLORS[i % len(_RACK_COLORS)]
        fig.add_trace(
            go.Scatter(
                x=group["t"],
                y=group["demand_kw"],
                mode="lines",
                name=f"{rack_name} demand",
                line={"color": color, "dash": "dot"},
                legendgroup=rack_name,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=group["t"],
                y=group["consumed_kw"],
                mode="lines",
                name=f"{rack_name} draw",
                line={"color": color},
                legendgroup=rack_name,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=group["t"],
                y=group["temp_c"],
                mode="lines",
                name=rack_name,
                line={"color": color},
                legendgroup=rack_name,
                showlegend=False,
            ),
            row=2,
            col=1,
        )

    fig.add_hline(
        y=rack.throttle_temp_c, line_dash="dot", line_color="orange", annotation_text="Tj throttle", row=2, col=1
    )
    fig.add_hline(y=rack.shutdown_temp_c, line_dash="dot", line_color="red", annotation_text="shutdown", row=2, col=1)

    start_s, end_s = _window_bounds(scenario, window)
    if start_s is not None:
        fig.add_vrect(x0=start_s, x1=end_s, fillcolor="blue", opacity=0.10, line_width=0, row="all", col="all")

    fig.update_yaxes(title_text="kW", row=1, col=1)
    fig.update_yaxes(title_text="°C", row=2, col=1)
    fig.update_xaxes(title_text="t, s", row=2, col=1)
    fig.update_layout(height=650, margin={"t": 50}, legend={"tracegroupgap": 0})
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
        "Energy flow (Sankey): demand vs delivered/curtailed, draw vs IT load/PSU loss, heat vs liquid/air",
        "Cooling by channel: liquid vs air removed over time, against nameplate capacity",
        "Per-rack detail: demand vs draw, and temperature, per rack",
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
        '"Power available" (an energy input) and "cooling available" (a heat-removal capacity) are different '
        "physical quantities, not two flavors of the same thing, even though both are measured in kW.",
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

    app.layout = html.Div(
        [
            html.H2(f"Sim-0 (energy flow view): {scenario_name}"),
            html.Div(
                "Every arrow is a kW flow. Left = what came in. Right = what left as heat. "
                "Nothing is skipped: what flows into a node always equals what flows out of it.",
                style={"color": "#666", "marginBottom": "1rem", "fontStyle": "italic"},
            ),
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
            html.Div(
                [
                    html.Label("Time window", style={"fontWeight": "bold", "marginRight": "0.5rem"}),
                    dcc.Dropdown(
                        id="window-dropdown",
                        options=_window_options(scenario),
                        value="whole",
                        clearable=False,
                        style={"width": "280px"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center", "marginBottom": "1rem"},
            ),
            dcc.Graph(id="sankey-graph"),
            dcc.Graph(id="context-graph"),
            dcc.Graph(id="cooling-graph"),
            html.H3("Per-rack detail"),
            dcc.Graph(id="detail-graph"),
        ],
        style={"fontFamily": "sans-serif", "padding": "1.5rem"},
    )

    @app.callback(
        Output("sankey-graph", "figure"),
        Output("context-graph", "figure"),
        Output("cooling-graph", "figure"),
        Output("detail-graph", "figure"),
        Input("window-dropdown", "value"),
    )
    def _update(window: str):
        selected = _select(df, window, scenario)
        totals = _flow_totals(selected)
        window_labels = {o["value"]: o["label"] for o in _window_options(scenario)}
        title = f"Energy flow — {window_labels[window]}"
        return (
            _sankey_figure(totals, title),
            _context_figure(display_df, scenario, window),
            _cooling_figure(display_df, kpis, scenario, window),
            _detail_figure(display_df, scenario, window),
        )

    return app
