"""Column schema, the event log, and the series contract shared by charts and viz.

Defined once here so that the analysis figures and the visualization payload
cannot drift apart, and so that adding a component means adding one telemetry
dict rather than editing three lists of column names.
"""

from dataclasses import dataclass

import pandas as pd

STATE_COLUMNS: tuple[str, ...] = (
    "a_grid_state",
    "b_grid_state",
    "a_tx_state",
    "b_tx_state",
    "a_ats_state",
    "b_ats_state",
    "a_ups_state",
    "b_ups_state",
    "gen_state",
    "cool_chiller_state",
    "cool_cdu_state",
    "cool_crah_state",
)
"""Facility columns whose changes become entries in the event log."""


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    """How one column survives downsampling and quantization.

    ``agg`` is not cosmetic. Averaging a state code is meaningless, and
    averaging a rate over a bucket is the only aggregation that preserves
    energy. ``companion`` ships a second series for columns where the *peak* is
    the point: a 60 second 105 % UPS overload averaged into a five-minute bucket
    vanishes, which would downsample away the finding a scenario exists to show.
    """

    column: str
    scale: float
    agg: str = "mean"
    companion: str | None = None
    label: str = ""

    @property
    def companion_column(self) -> str | None:
        return None if self.companion is None else f"{self.column}__{self.companion}"


_KW = 0.1
_PCT = 0.1
_TEMP = 0.01
_FRAC = 0.0001

FACILITY_SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("it_demand_kw", _KW, label="IT demand"),
    SeriesSpec("it_drawn_kw", _KW, label="IT drawn"),
    SeriesSpec("it_unserved_kw", _KW, companion="max", label="IT unserved"),
    SeriesSpec("mech_kw", _KW, label="Cooling electrical"),
    SeriesSpec("loss_kw", _KW, label="Conversion loss"),
    SeriesSpec("facility_kw", _KW, companion="max", label="Facility total"),
    SeriesSpec("charge_kw", _KW, label="Battery charging"),
    SeriesSpec("gen_output_kw", _KW, label="Generator output"),
    SeriesSpec("gen_fuel_l", _KW, agg="last", label="Fuel remaining"),
    SeriesSpec("gen_fuel_rate_l_per_h", _KW, label="Fuel burn rate"),
    SeriesSpec("a_delivered_kw", _KW, companion="max", label="Side A delivered"),
    SeriesSpec("b_delivered_kw", _KW, companion="max", label="Side B delivered"),
    # Per-side detail: the flow diagram labels every box from these, so the
    # facility-wide totals alone are not enough. `grid_kw` is derived in JS from
    # the two sides rather than shipped a third time.
    SeriesSpec("a_grid_kw", _KW, label="Side A from utility"),
    SeriesSpec("b_grid_kw", _KW, label="Side B from utility"),
    SeriesSpec("a_generator_kw", _KW, label="Side A from genset"),
    SeriesSpec("b_generator_kw", _KW, label="Side B from genset"),
    SeriesSpec("a_ups_output_kw", _KW, label="UPS A output"),
    SeriesSpec("b_ups_output_kw", _KW, label="UPS B output"),
    SeriesSpec("a_tx_load_pct", _PCT, label="Transformer A load"),
    SeriesSpec("b_tx_load_pct", _PCT, label="Transformer B load"),
    SeriesSpec("a_tx_winding_c", _TEMP, agg="last", companion="max", label="Winding A"),
    SeriesSpec("b_tx_winding_c", _TEMP, agg="last", companion="max", label="Winding B"),
    SeriesSpec("a_ups_load_pct", _PCT, companion="max", label="UPS A load"),
    SeriesSpec("b_ups_load_pct", _PCT, companion="max", label="UPS B load"),
    SeriesSpec("a_batt_soc", _FRAC, agg="last", companion="min", label="Battery A SoC"),
    SeriesSpec("b_batt_soc", _FRAC, agg="last", companion="min", label="Battery B SoC"),
    SeriesSpec("a_batt_out_kw", _KW, label="Battery A output"),
    SeriesSpec("b_batt_out_kw", _KW, label="Battery B output"),
    SeriesSpec("a_autonomy_s", 1.0, agg="last", companion="min", label="Autonomy A"),
    SeriesSpec("b_autonomy_s", 1.0, agg="last", companion="min", label="Autonomy B"),
    SeriesSpec("rack_temp_max_c", _TEMP, companion="max", label="Hottest rack"),
    SeriesSpec("rack_temp_mean_c", _TEMP, label="Mean rack temp"),
    SeriesSpec("cool_loop_supply_c", _TEMP, agg="last", companion="max", label="Loop supply"),
    SeriesSpec("cool_loop_return_c", _TEMP, agg="last", label="Loop return"),
    SeriesSpec("cool_crah_room_c", _TEMP, agg="last", companion="max", label="Room air"),
    SeriesSpec("cool_liquid_heat_kw", _KW, label="Heat into loop"),
    SeriesSpec("cool_air_heat_kw", _KW, label="Heat into room"),
    SeriesSpec("cool_rejected_liquid_kw", _KW, label="Rejected via liquid"),
    SeriesSpec("cool_rejected_air_kw", _KW, label="Rejected via air"),
    SeriesSpec("cool_chiller_capacity_kw", _KW, agg="last", label="Chiller capacity"),
    SeriesSpec("pue", 0.001, label="PUE"),
    SeriesSpec("racks_down", 1.0, agg="max", label="Racks down"),
    SeriesSpec("balance_residual_kw", 0.001, companion="max", label="Balance residual"),
)

RACK_SERIES: tuple[SeriesSpec, ...] = (
    SeriesSpec("drawn_kw", _KW, label="Draw"),
    SeriesSpec("temp_c", _TEMP, companion="max", label="Temperature"),
    SeriesSpec("removed_kw", _KW, label="Heat removed"),
)

RIBBON_COLUMNS: tuple[str, ...] = (
    "a_grid_state",
    "b_grid_state",
    "a_ups_state",
    "b_ups_state",
    "a_ats_state",
    "b_ats_state",
    "gen_state",
    "cool_chiller_state",
)
"""State columns rendered as coloured bands under the charts. Cheapest element
to build and the most informative for reading a failure narrative."""

COLUMN_GROUPS: dict[str, tuple[str, ...]] = {
    "demand": ("it_demand_kw", "it_drawn_kw", "it_unserved_kw", "mech_kw", "loss_kw", "facility_kw"),
    "sideA": ("a_delivered_kw", "a_grid_kw", "a_generator_kw", "a_batt_out_kw", "a_ups_load_pct", "a_batt_soc"),
    "sideB": ("b_delivered_kw", "b_grid_kw", "b_generator_kw", "b_batt_out_kw", "b_ups_load_pct", "b_batt_soc"),
    "genset": ("gen_output_kw", "gen_fuel_l", "gen_fuel_rate_l_per_h", "gen_run_h"),
    "cooling": (
        "cool_liquid_heat_kw",
        "cool_air_heat_kw",
        "cool_rejected_liquid_kw",
        "cool_rejected_air_kw",
        "cool_chiller_capacity_kw",
    ),
    "thermal": ("rack_temp_max_c", "rack_temp_mean_c", "cool_loop_supply_c", "cool_loop_return_c", "cool_crah_room_c"),
    "efficiency": ("pue", "balance_residual_kw"),
    "cost": ("energy_cost_usd", "diesel_cost_usd", "unserved_cost_usd", "downtime_cost_usd"),
}


def build_events(facility_df: pd.DataFrame, racks_df: pd.DataFrame, scheduled: list[dict]) -> pd.DataFrame:
    """Every discrete transition in the run, as one frame.

    Derived from the frames rather than emitted by the components, so no
    component needs to know an event bus exists and the log cannot disagree with
    the time series it was built from.
    """
    rows: list[dict] = list(scheduled)

    for column in STATE_COLUMNS:
        if column not in facility_df.columns:
            continue
        series = facility_df[column]
        changed = series != series.shift(1)
        changed.iloc[0] = False
        for idx in facility_df.index[changed]:
            rows.append(
                {
                    "t": float(facility_df.at[idx, "t"]),
                    "component": column.removesuffix("_state"),
                    "kind": "transition",
                    "from_state": str(series.shift(1).at[idx]),
                    "to_state": str(series.at[idx]),
                    "detail": "",
                }
            )

    if not racks_df.empty:
        for rack, group in racks_df.groupby("rack", sort=True):
            group = group.sort_values("t")
            states = group["state"]
            changed = states != states.shift(1)
            changed.iloc[0] = False
            for idx in group.index[changed]:
                rows.append(
                    {
                        "t": float(group.at[idx, "t"]),
                        "component": str(rack),
                        "kind": "rack",
                        "from_state": str(states.shift(1).at[idx]),
                        "to_state": str(states.at[idx]),
                        "detail": "",
                    }
                )

    if not rows:
        return pd.DataFrame(columns=["t", "component", "kind", "from_state", "to_state", "detail"])
    return pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
