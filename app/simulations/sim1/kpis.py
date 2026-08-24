"""Run summary. Pure functions over the frames, plus counters read off the site.

Two traps are encoded here rather than left to whoever reads the numbers.

``pue_avg`` is the ratio of *sums*, not the mean of per-tick ratios: averaging
ratios weights a tick where IT is nearly zero as heavily as a tick at full load,
which produces nonsense during an outage.

Every aggregate integrates against the tick's own ``dt``, because the engine's
timebase is non-uniform. ``mean()`` over these rows would silently weight a one
second tick the same as a sixty second one.
"""

from typing import Any

import pandas as pd

from app.simulations.sim1.electrical.overload import OverloadMonitor
from app.simulations.sim1.facility import Facility
from app.simulations.sim1.units import SECONDS_PER_HOUR


def scalar(value: Any) -> float:
    """Narrow a pandas reduction to a plain float.

    ``Series.max()`` and friends are typed as possibly-Series, so every call site
    would otherwise need its own cast. One helper keeps the KPI table readable.
    """
    return float(value)


def energy_kwh(df: pd.DataFrame, column: str) -> float:
    """``sum(power * dt)`` — the only correct way to integrate these frames."""
    if df.empty or column not in df.columns:
        return 0.0
    return scalar((df[column].fillna(0.0) * df["dt"]).sum() / SECONDS_PER_HOUR)


def seconds_where(df: pd.DataFrame, mask: Any) -> float:
    if df.empty:
        return 0.0
    return scalar(df.loc[mask, "dt"].sum())


def count_transitions(states: Any, target: str) -> int:
    return int(((states == target) & (states.shift(1) != target)).sum())


def _overload_seconds(facility: Facility) -> float:
    monitors: list[OverloadMonitor] = [facility.genset.monitor]
    for side in facility.sides:
        monitors.extend([side.transformer.monitor, side.ups.monitor, side.pdu.monitor])
    return float(sum(monitor.overload_s for monitor in monitors))


def summarize(facility_df: pd.DataFrame, racks_df: pd.DataFrame, facility: Facility) -> dict[str, float | str | None]:
    if facility_df.empty:
        return {"uptime_pct": 100.0, "it_energy_kwh": 0.0, "facility_energy_kwh": 0.0}

    total_s = scalar(facility_df["dt"].sum())
    down_s = seconds_where(facility_df, facility_df["site_down"] > 0.0)

    it_kwh = energy_kwh(facility_df, "it_drawn_kw")
    want_kwh = energy_kwh(facility_df, "it_demand_kw")
    facility_kwh = energy_kwh(facility_df, "facility_kw")
    headroom = facility_df["cool_chiller_capacity_kw"] - facility_df["cool_liquid_heat_kw"]

    pue = facility_df["pue"].astype(float)
    finite_pue = pue.dropna()

    throttle_events = 0
    shutdown_events = 0
    if not racks_df.empty:
        for _, group in racks_df.groupby("rack", sort=True):
            states = group.sort_values("t")["state"]
            throttle_events += count_transitions(states, "throttling")
            shutdown_events += count_transitions(states, "emergency_shutdown")

    on_battery = (facility_df["a_batt_out_kw"] > 1e-6) | (facility_df["b_batt_out_kw"] > 1e-6)
    autonomy = pd.concat([facility_df["a_autonomy_s"], facility_df["b_autonomy_s"]]).astype(float).dropna()

    return {
        # Time the hall was drawing nothing at all.
        "uptime_pct": round(100.0 * (total_s - down_s) / total_s, 3) if total_s > 0 else 100.0,
        # Energy actually served against energy the workload asked for. Uptime
        # is binary and misses graceful degradation; this is what throttling and
        # curtailment cost.
        "served_pct": round(100.0 * it_kwh / want_kwh, 3) if want_kwh > 0.0 else 100.0,
        "duration_s": total_s,
        "ticks": float(len(facility_df)),
        "it_energy_kwh": round(it_kwh, 3),
        "it_demand_kwh": round(want_kwh, 3),
        "facility_energy_kwh": round(facility_kwh, 3),
        "grid_energy_kwh": round(energy_kwh(facility_df, "grid_kw"), 3),
        "cooling_energy_kwh": round(energy_kwh(facility_df, "mech_kw"), 3),
        "loss_energy_kwh": round(energy_kwh(facility_df, "loss_kw"), 3),
        "unserved_energy_kwh": round(energy_kwh(facility_df, "it_unserved_kw"), 3),
        # Ratio of sums. None only when no IT energy was served at all, in which
        # case PUE is genuinely undefined rather than large.
        "pue_avg": round(facility_kwh / it_kwh, 4) if it_kwh > 0.0 else None,
        "pue_p95": round(scalar(finite_pue.quantile(0.95)), 4) if not finite_pue.empty else None,
        "throttle_events": float(throttle_events),
        "shutdown_events": float(shutdown_events),
        "time_on_battery_s": round(seconds_where(facility_df, on_battery), 3),
        "battery_min_soc": round(
            min(side.ups.battery.min_soc_seen for side in facility.sides),
            5,
        ),
        "worst_autonomy_s": round(scalar(autonomy.min()), 1) if not autonomy.empty else None,
        "gen_starts": float(facility.genset.starts),
        "gen_start_failures": float(facility.genset.start_failures),
        "gen_run_h": round(facility.genset.run_hours, 4),
        "diesel_l": round(facility.genset.tank.consumed_l, 3),
        "fuel_remaining_l": round(facility.genset.tank.level_l, 3),
        "max_ups_load_pct_a": round(100.0 * facility.side_a.ups.monitor.peak_ratio, 2),
        "max_ups_load_pct_b": round(100.0 * facility.side_b.ups.monitor.peak_ratio, 2),
        "ups_a_final_state": facility.side_a.ups.state.value,
        "ups_b_final_state": facility.side_b.ups.state.value,
        "overload_seconds": round(_overload_seconds(facility), 3),
        "side_failover_events": float(facility.side_a.ats.transfers + facility.side_b.ats.transfers),
        "rack_peak_temp_c": round(scalar(facility_df["rack_temp_max_c"].max()), 2),
        "chiller_min_headroom_kw": round(scalar(headroom.min()), 2),
        "loop_peak_c": round(facility.cooling.loop.peak_supply_c, 2),
        "room_peak_c": round(facility.cooling.crah.peak_room_c, 2),
        "max_balance_residual_kw": round(scalar(facility_df["balance_residual_kw"].abs().max()), 9),
        "energy_cost_usd": round(scalar(facility_df["energy_cost_usd"].sum()), 2),
        "diesel_cost_usd": round(scalar(facility_df["diesel_cost_usd"].sum()), 2),
        "unserved_cost_usd": round(scalar(facility_df["unserved_cost_usd"].sum()), 2),
        "downtime_cost_usd": round(scalar(facility_df["downtime_cost_usd"].sum()), 2),
        "total_cost_usd": round(
            scalar(
                facility_df["energy_cost_usd"].sum()
                + facility_df["diesel_cost_usd"].sum()
                + facility_df["unserved_cost_usd"].sum()
                + facility_df["downtime_cost_usd"].sum()
            ),
            2,
        ),
    }
