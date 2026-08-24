"""The clock: a fixed-step SimPy loop whose step size is not fixed.

At dt=60 the entire five-minute battery story is five samples wide and a 30
second generator start cannot be represented at all. Rather than run a whole
week at one second, the engine refines dt inside windows derived from the
scenario's event schedule, and lands ticks exactly on event times.

The consequence propagates everywhere: dt is non-uniform, so every rate must be
integrated with ``ctx.dt`` and every aggregate must be ``sum(rate * dt)``. Any
code that counts rows or takes a plain mean over these frames is wrong.
"""

from collections.abc import Generator
from dataclasses import dataclass

import pandas as pd
import simpy

from app.simulations.sim1 import kpis as kpi_module
from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.scenarios import ScenarioConfig, build_facility, get_scenario
from app.simulations.sim1.telemetry import build_events


@dataclass(frozen=True, slots=True)
class RunResult:
    scenario: str
    description: str
    facility: pd.DataFrame
    racks: pd.DataFrame
    events: pd.DataFrame
    kpis: dict[str, float | str | None]
    duration_s: float
    dt_s: float
    dt_fine_s: float


def _in_window(t: float, windows: tuple[tuple[float, float], ...]) -> bool:
    return any(low <= t < high for low, high in windows)


def _next_boundary(t: float, boundaries: tuple[float, ...]) -> float | None:
    ahead = [b for b in boundaries if b > t + 1e-9]
    return min(ahead) if ahead else None


def _tick_dt(
    t: float,
    windows: tuple[tuple[float, float], ...],
    boundaries: tuple[float, ...],
    dt_coarse: float,
    dt_fine: float,
    until: float,
) -> float:
    dt = dt_fine if _in_window(t, windows) else dt_coarse
    boundary = _next_boundary(t, boundaries)
    if boundary is not None:
        # Clamping to the next boundary is what makes an event fire on the exact
        # second it was scheduled for, rather than up to one coarse tick late.
        dt = min(dt, boundary - t)
    return max(1e-6, min(dt, until - t))


def run_scenario(
    scenario_name: str,
    duration_s: float | None = None,
    dt: float | None = None,
    dt_fine: float | None = None,
    seed: int | None = None,
) -> RunResult:
    scenario: ScenarioConfig = get_scenario(scenario_name)
    duration = scenario.default_duration_s if duration_s is None else duration_s
    dt_coarse = scenario.default_dt_s if dt is None else dt
    dt_refined = scenario.default_dt_fine_s if dt_fine is None else dt_fine
    if dt_refined <= 0.0 or dt_refined > dt_coarse:
        dt_refined = dt_coarse

    facility = build_facility(scenario, seed=seed)
    windows = facility.schedule.refinement_windows(lead_s=2.0 * dt_coarse)
    boundaries = tuple(sorted({*facility.schedule.boundaries(), *(w for pair in windows for w in pair)}))

    facility_rows: list[dict] = []
    rack_rows: list[dict] = []

    env = simpy.Environment()

    def tick() -> Generator[simpy.Event, None, None]:
        index = 0
        while True:
            t = float(env.now)
            step = _tick_dt(t, windows, boundaries, dt_coarse, dt_refined, duration)
            ctx = TickContext(t=t, dt=step, tick=index)
            facility_rows.append(facility.tick(ctx))
            for rack in facility.racks:
                row: dict[str, float | str] = {"t": t, "dt": step, "rack": rack.name}
                row.update(rack.telemetry())
                rack_rows.append(row)
            index += 1
            yield env.timeout(step)

    env.process(tick())
    env.run(until=duration)

    facility_df = pd.DataFrame(facility_rows)
    racks_df = pd.DataFrame(rack_rows)
    scheduled = [
        {
            "t": event.t,
            "component": event.target,
            "kind": "scheduled",
            "from_state": "",
            "to_state": event.action,
            "detail": event.described(),
        }
        for event in facility.fired_events
    ]
    events_df = build_events(facility_df, racks_df, scheduled)

    return RunResult(
        scenario=scenario.name,
        description=scenario.description,
        facility=facility_df,
        racks=racks_df,
        events=events_df,
        kpis=kpi_module.summarize(facility_df, racks_df, facility),
        duration_s=duration,
        dt_s=dt_coarse,
        dt_fine_s=dt_refined,
    )
