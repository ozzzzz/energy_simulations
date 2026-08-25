import pandas as pd
from dash import Dash, Input, Output, dcc, html

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
from app.simulations.sim0.rack import Rack
from app.simulations.sim0.scenarios import get_scenario


def _kpi_card(label: str, value: str) -> html.Div:
    return html.Div(
        [html.Div(label, style={"color": "#888"}), html.H3(value, style={"margin": 0})],
        style={"padding": "0.75rem 1.5rem", "border": "1px solid #ddd", "borderRadius": "8px"},
    )


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
    display_df = resample_for_display(df)

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
                        options=window_options(scenario),
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
        selected = select(df, window, scenario)
        totals = flow_totals(selected)
        window_labels = {o["value"]: o["label"] for o in window_options(scenario)}
        title = f"Energy flow — {window_labels[window]}"
        return (
            sankey_figure(totals, title),
            context_figure(display_df, scenario, window),
            cooling_figure(display_df, kpis, scenario, window),
            detail_figure(display_df, scenario, window),
        )

    return app
