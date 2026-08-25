"""Conservation. The gate everything else in sim1 depends on."""

import pandas as pd
import pytest

from app.simulations.sim1.electrical.generator import GeneratorState
from app.simulations.sim1.engine import run_scenario
from app.simulations.sim1.facility import Facility
from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.scenarios import build_facility, get_scenario


def _drive(scenario_name: str, ticks: int, dt: float) -> tuple[Facility, pd.DataFrame, dict[str, float]]:
    facility = build_facility(get_scenario(scenario_name))
    before = {
        "racks_kws": sum(rack.thermal_mass_kws_per_c * rack.temp_c for rack in facility.racks),
        "loop_c": facility.cooling.loop.supply_c,
        "room_c": facility.cooling.crah.room_c,
    }
    rows = [facility.tick(TickContext(t=i * dt, dt=dt, tick=i)) for i in range(ticks)]
    return facility, pd.DataFrame(rows), before


@pytest.mark.parametrize("scenario", ["normal", "grid_outage_gen_fail", "cooling_failure"])
def test_every_tick_closes_its_electrical_balance(scenario: str) -> None:
    """grid + generator + battery discharge == IT + cooling + losses + charging."""
    _, df, _ = _drive(scenario, ticks=300, dt=10.0)

    assert df["balance_residual_kw"].abs().max() < 1e-6
    reconstructed = df["a_grid_kw"] + df["b_grid_kw"] + df["gen_output_kw"] + df["a_batt_out_kw"] + df["b_batt_out_kw"]
    assert (reconstructed - df["sources_kw"]).abs().max() < 1e-9


def test_rack_heat_splits_between_the_two_channels_by_the_capture_rate() -> None:
    facility, df, _ = _drive("normal", ticks=120, dt=30.0)
    capture = facility.racks[0].liquid_capture_rate
    total = df["cool_liquid_heat_kw"] + df["cool_air_heat_kw"]

    assert (df["cool_liquid_heat_kw"] / total).round(9).eq(round(capture, 9)).all()
    # Heat reaching the sinks is the draw minus whatever the racks banked this
    # tick, so it may briefly exceed the draw while they cool down.
    assert total.iloc[-1] == pytest.approx(df["it_drawn_kw"].iloc[-1], rel=0.05)


def test_the_thermal_balance_closes_including_stored_enthalpy() -> None:
    """The stored terms are the easy ones to forget, and their absence is how the
    one lagged edge in the tick would hide a real error."""
    facility, df, before = _drive("normal", ticks=200, dt=30.0)
    dt = df["dt"]

    generated_kws = float((df["it_drawn_kw"] * dt).sum())
    to_sink_kws = float(((df["cool_liquid_heat_kw"] + df["cool_air_heat_kw"]) * dt).sum())
    racks_after = sum(rack.thermal_mass_kws_per_c * rack.temp_c for rack in facility.racks)
    delta_racks_kws = racks_after - before["racks_kws"]

    assert generated_kws == pytest.approx(delta_racks_kws + to_sink_kws, rel=1e-9)

    rejected_kws = float(((df["cool_rejected_liquid_kw"] + df["cool_rejected_air_kw"]) * dt).sum())
    loop = facility.cooling.loop
    crah = facility.cooling.crah
    delta_loop_kws = loop.mass_kws_per_c * (loop.supply_c - before["loop_c"])
    delta_room_kws = crah.mass_kws_per_c * (crah.room_c - before["room_c"])

    assert to_sink_kws == pytest.approx(delta_loop_kws + delta_room_kws + rejected_kws, rel=1e-9)


def test_cooling_outranks_it_when_power_runs_short() -> None:
    """A deep brownout shows IT at zero while the pumps still turn. That is the
    policy, not a bug: losing the chillers cooks the hall, losing GPU-seconds
    does not."""
    facility = build_facility(get_scenario("normal"))
    facility.side_a.transformer.trip()
    facility.side_b.transformer.trip()
    facility.side_a.ups.battery.soc = facility.side_a.ups.battery.cutoff_soc
    facility.side_b.ups.battery.soc = facility.side_b.ups.battery.cutoff_soc
    # Without this the genset simply starts and carries the site, which is the
    # correct behaviour but not what this test is about.
    facility.genset.state = GeneratorState.FAILED

    rows = [facility.tick(TickContext(t=i * 10.0, dt=10.0, tick=i)) for i in range(20)]
    df = pd.DataFrame(rows)

    assert df["it_drawn_kw"].iloc[-1] == pytest.approx(0.0)
    assert df["site_down"].iloc[-1] == 1.0
    assert df["mech_demand_kw"].iloc[-1] > 0.0


def test_the_generator_is_only_summoned_when_both_sides_lose_their_mains() -> None:
    """A single transformer failure in a 2N site does not warrant the genset —
    the other side has full capacity."""
    facility = build_facility(get_scenario("normal"))
    facility.side_a.transformer.trip()
    for i in range(100):
        facility.tick(TickContext(t=i * 10.0, dt=10.0, tick=i))
    assert facility.genset.starts == 0

    facility.side_b.transformer.trip()
    for i in range(100, 200):
        facility.tick(TickContext(t=i * 10.0, dt=10.0, tick=i))
    assert facility.genset.starts == 1


def test_a_run_lands_ticks_exactly_on_its_scheduled_events() -> None:
    result = run_scenario("grid_outage_gen_ok")
    times = set(result.facility["t"])
    for event_t in (600.0, 1500.0):
        assert event_t in times

    fired = result.events[result.events["kind"] == "scheduled"]
    assert set(fired["t"]) == {600.0, 1500.0}


def test_dt_is_refined_around_events_and_coarse_elsewhere() -> None:
    result = run_scenario("grid_outage_gen_ok", duration_s=3600.0, dt=10.0, dt_fine=1.0)
    df = result.facility

    near_event = df[(df["t"] >= 600.0) & (df["t"] < 800.0)]
    far_from_event = df[(df["t"] > 3000.0) & (df["t"] < 3500.0)]

    assert near_event["dt"].max() == 1.0
    assert far_from_event["dt"].min() == 10.0
