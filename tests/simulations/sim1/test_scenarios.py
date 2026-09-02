"""One assertion set per scenario, about the thing that scenario exists to show."""

import pytest

from app.simulations.sim1.engine import run_scenario


def test_normal_runs_clean_and_never_touches_its_reserves() -> None:
    result = run_scenario("normal", duration_s=6 * 3600.0)
    k = result.kpis

    assert k["uptime_pct"] == 100.0
    assert k["served_pct"] == 100.0
    assert k["shutdown_events"] == 0.0
    assert k["throttle_events"] == 0.0
    assert 1.2 < float(k["pue_avg"]) < 1.45
    assert k["battery_min_soc"] == 1.0
    assert k["time_on_battery_s"] == 0.0
    assert k["gen_starts"] == 0.0
    assert k["diesel_l"] == 0.0
    # Both sides carry the same share, tick by tick.
    df = result.facility
    assert (df["a_delivered_kw"] - df["b_delivered_kw"]).abs().max() < 1e-6


def test_grid_outage_with_a_working_genset_costs_only_battery_seconds() -> None:
    result = run_scenario("grid_outage_gen_ok")
    k = result.kpis

    assert k["uptime_pct"] == 100.0
    assert k["served_pct"] == 100.0
    # A 30 s crank plus a 1 s transfer, and nothing more.
    assert 30.0 <= float(k["time_on_battery_s"]) <= 90.0
    assert k["gen_starts"] == 1.0
    assert k["gen_start_failures"] == 0.0
    assert float(k["diesel_l"]) > 0.0
    assert 0.5 < float(k["battery_min_soc"]) < 1.0
    assert k["side_failover_events"] == 4.0, "both sides transfer out and back"


def test_batteries_recharge_once_the_mains_return() -> None:
    result = run_scenario("grid_outage_gen_ok")
    df = result.facility
    trough = df.loc[df["a_batt_soc"].idxmin()]
    after = df[df["t"] > float(trough["t"])]

    assert float(after["a_batt_soc"].iloc[-1]) > float(trough["a_batt_soc"])
    assert float(df["charge_kw"].max()) > 0.0


def test_grid_outage_with_a_dead_genset_takes_the_site_down() -> None:
    result = run_scenario("grid_outage_gen_fail")
    k = result.kpis

    assert k["gen_start_failures"] == 3.0
    assert k["gen_starts"] == 0.0
    assert float(k["battery_min_soc"]) == pytest.approx(0.05)
    assert float(k["uptime_pct"]) < 60.0
    assert float(k["unserved_energy_kwh"]) > 0.0
    assert k["ups_a_final_state"] == "offline"
    assert k["ups_b_final_state"] == "offline"
    assert float(k["downtime_cost_usd"]) > 0.0


def test_a_peak_load_spike_is_survivable_but_cooling_is_the_tight_margin() -> None:
    result = run_scenario("load_spike")
    k = result.kpis

    assert k["uptime_pct"] == 100.0
    assert k["served_pct"] == 100.0
    assert k["shutdown_events"] == 0.0
    # Chiller headroom nearly gone...
    assert 0.0 < float(k["chiller_min_headroom_kw"]) < 80.0
    # ...while each UPS is still around half loaded.
    assert float(k["max_ups_load_pct_b"]) < 60.0


def test_a_chiller_trip_throttles_then_shuts_down_and_frees_its_own_power() -> None:
    result = run_scenario("cooling_failure")
    k = result.kpis
    df = result.facility

    assert float(k["throttle_events"]) > 0.0
    assert float(k["shutdown_events"]) > 0.0
    assert float(k["loop_peak_c"]) > 60.0
    assert float(k["uptime_pct"]) < 100.0

    fault_t = 15 * 60.0
    before = df[(df["t"] > fault_t - 300.0) & (df["t"] < fault_t)]
    during = df[(df["t"] > fault_t + 300.0) & (df["t"] < fault_t + 2700.0)]

    # The loop climbs monotonically while the chiller is out...
    loop = during["cool_loop_supply_c"].tolist()
    assert loop == sorted(loop)
    # ...and the chiller's own electrical draw leaves the power stack.
    assert float(before["cool_chiller_electrical_kw"].mean()) > 50.0
    assert float(during["cool_chiller_electrical_kw"].max()) == 0.0


def test_losing_a_side_drains_that_battery_then_hands_the_site_over() -> None:
    result = run_scenario("side_a_lost")
    k = result.kpis

    assert k["uptime_pct"] == 100.0
    assert k["served_pct"] == 100.0
    # UPS A has no input, so it quietly burns its string down...
    assert float(k["battery_min_soc"]) == pytest.approx(0.05)
    assert k["ups_a_final_state"] == "offline"
    # ...and B ends up carrying the whole site, close to but under its nameplate.
    assert 85.0 < float(k["max_ups_load_pct_b"]) < 100.0
    assert k["ups_b_final_state"] == "online"
    # A single transformer loss does not warrant the genset in a 2N site.
    assert k["gen_starts"] == 0.0


def test_losing_a_side_at_peak_pushes_the_survivor_into_bypass() -> None:
    result = run_scenario("side_a_lost_at_peak")
    k = result.kpis

    assert float(k["max_ups_load_pct_b"]) > 100.0
    assert float(k["overload_seconds"]) >= 60.0
    # The load survives the overload — but silently loses battery protection.
    assert k["ups_b_final_state"] == "bypass"
    assert k["uptime_pct"] == 100.0


def test_undersized_cords_turn_a_side_loss_into_curtailment() -> None:
    result = run_scenario("undersized_cords")
    k = result.kpis

    assert k["uptime_pct"] == 100.0
    assert float(k["served_pct"]) < 95.0
    assert float(k["unserved_energy_kwh"]) > 100.0
    assert float(k["unserved_cost_usd"]) > 0.0
    # The survivor is nowhere near its own limit — the cords are the constraint.
    assert float(k["max_ups_load_pct_b"]) < 70.0
    # And the curtailment lands on users, not just on kilowatts.
    assert float(k["request_drop_pct"]) > 1.0
    assert float(k["p95_queue_latency_s"]) > 0.0


def test_every_scenario_conserves_energy_and_produces_all_three_frames() -> None:
    for name in ("normal", "grid_outage_gen_ok", "cooling_failure", "undersized_cords"):
        result = run_scenario(name, duration_s=3600.0)
        assert float(result.kpis["max_balance_residual_kw"]) < 1e-6, name
        assert not result.facility.empty
        assert not result.racks.empty
        assert list(result.events.columns) == ["t", "component", "kind", "from_state", "to_state", "detail"]


def test_a_run_is_reproducible_for_a_given_seed() -> None:
    first = run_scenario("normal", duration_s=1800.0, seed=42)
    second = run_scenario("normal", duration_s=1800.0, seed=42)
    other = run_scenario("normal", duration_s=1800.0, seed=43)

    assert first.kpis["it_energy_kwh"] == second.kpis["it_energy_kwh"]
    assert first.kpis["it_energy_kwh"] != other.kpis["it_energy_kwh"]
