from collections.abc import Generator

import pandas as pd
import simpy

from app.simulations.sim0.rack import Rack, RackState
from app.simulations.sim0.scenarios import get_scenario


def run_scenario(scenario_name: str, duration_s: float = 600.0, dt: float = 1.0) -> tuple[pd.DataFrame, dict]:
    scenario = get_scenario(scenario_name)
    racks = [Rack(name=f"rack-{i + 1}") for i in range(scenario.n_racks)]
    workloads = scenario.build_workloads()
    rows: list[dict] = []

    env = simpy.Environment()

    def tick() -> Generator[simpy.Event, None, None]:
        prev_total_consumed_kw = 0.0
        while True:
            t = env.now
            demand_per_rack_kw = [w.demand_kw(t) for w in workloads]
            total_demand_kw = sum(demand_per_rack_kw)

            power_available_kw = scenario.power.step(t=t, dt=dt, requested_kw=total_demand_kw)
            power_scale = 1.0 if total_demand_kw <= 0 else min(1.0, power_available_kw / total_demand_kw)

            # cooling reacts to the *previous* tick's heat load: this tick's actual
            # consumption isn't known until after Rack.step runs below, and a real
            # Cooling Plant would only see load after the fact anyway (one-tick lag).
            cooling_available_kw = scenario.cooling.step(t=t, dt=dt, requested_kw=prev_total_consumed_kw)
            cooling_share_kw = cooling_available_kw / len(racks)

            tick_consumed_kw = 0.0
            for rack, demand_kw in zip(racks, demand_per_rack_kw, strict=True):
                row = rack.step(
                    dt=dt,
                    demand_kw=demand_kw,
                    power_scale=power_scale,
                    cooling_available_kw=cooling_share_kw,
                )
                # liquid (GPU/CPU/NVSwitch cold plates) and air (OSFP/storage/PDB) carry
                # heat concurrently, not as a fallback — split by the rack's fixed ratio
                row["liquid_kw"] = row["consumed_kw"] * rack.liquid_capture_rate
                row["air_kw"] = row["consumed_kw"] * (1.0 - rack.liquid_capture_rate)
                row["t"] = t
                rows.append(row)
                tick_consumed_kw += row["consumed_kw"]

            prev_total_consumed_kw = tick_consumed_kw
            yield env.timeout(dt)

    env.process(tick())
    env.run(until=duration_s)

    df = pd.DataFrame(rows)
    kpis = _summarize(df, dt)
    kpis["cooling_liquid_capacity_kw"] = scenario.cooling.liquid_capacity_kw
    kpis["cooling_air_capacity_kw"] = scenario.cooling.air_capacity_kw
    return df, kpis


def _count_transitions(states: pd.Series, state: str) -> int:
    return int(((states == state) & (states.shift(1) != state)).sum())


def _summarize(df: pd.DataFrame, dt: float) -> dict:
    if df.empty:
        return {
            "uptime_pct": 100.0,
            "energy_kwh": 0.0,
            "delivery_efficiency_pct": 100.0,
            "throttle_events": 0,
            "shutdown_events": 0,
        }

    by_tick = df.groupby("t")["state"]
    total_ticks = by_tick.ngroups
    all_down_ticks = by_tick.apply(lambda s: (s == RackState.EMERGENCY_SHUTDOWN.value).all()).sum()
    uptime_pct = 100.0 * (total_ticks - all_down_ticks) / total_ticks

    total_draw_kw = df["consumed_kw"].sum()
    energy_kwh = total_draw_kw * dt / 3600.0
    # share of electrical draw that reaches the GPUs as useful compute power rather
    # than PSU/VRM conversion loss — a partial PUE-style figure (just the power-delivery
    # chain within the rack, not full facility PUE: cooling's own electrical draw and
    # upstream UPS/transformer loss aren't tracked, see docs/sim_0_plan.md).
    delivery_efficiency_pct = 100.0 * df["it_kw"].sum() / total_draw_kw if total_draw_kw > 0 else 100.0

    throttle_events = 0
    shutdown_events = 0
    for _, group in df.groupby("rack"):
        states = group.sort_values("t")["state"]
        throttle_events += _count_transitions(states, RackState.THROTTLING.value)
        shutdown_events += _count_transitions(states, RackState.EMERGENCY_SHUTDOWN.value)

    return {
        "uptime_pct": round(uptime_pct, 2),
        "energy_kwh": round(energy_kwh, 3),
        "delivery_efficiency_pct": round(delivery_efficiency_pct, 2),
        "throttle_events": throttle_events,
        "shutdown_events": shutdown_events,
    }
