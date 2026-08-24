"""KPI arithmetic, on a hand-built frame with deliberately mixed tick lengths."""

import pandas as pd
import pytest

from app.simulations.sim1 import kpis
from app.simulations.sim1.engine import run_scenario


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"t": 0.0, "dt": 1.0, "p_kw": 3600.0, "site_down": 0.0},
            {"t": 1.0, "dt": 3600.0, "p_kw": 1.0, "site_down": 1.0},
            {"t": 3601.0, "dt": 1.0, "p_kw": 3600.0, "site_down": 0.0},
        ]
    )


def test_energy_integrates_against_each_tick_own_dt() -> None:
    df = _frame()
    # sum(p * dt) = 3600*1 + 1*3600 + 3600*1 = 10800 kW*s = 3 kWh.
    assert kpis.energy_kwh(df, "p_kw") == pytest.approx(3.0)
    # The mean-times-count answer would be 2400.3 kWh — three orders out.
    assert df["p_kw"].mean() * len(df) / 3600.0 != pytest.approx(3.0)


def test_time_based_kpis_are_dt_weighted_not_row_counted() -> None:
    df = _frame()
    down_s = kpis.seconds_where(df, df["site_down"] > 0.0)
    assert down_s == 3600.0
    # Row counting would call this a third of the run; it is over 99.9 % of it.
    assert down_s / df["dt"].sum() > 0.999


def test_missing_columns_integrate_to_zero_rather_than_raising() -> None:
    assert kpis.energy_kwh(_frame(), "not_a_column") == 0.0
    assert kpis.energy_kwh(pd.DataFrame(), "p_kw") == 0.0


def test_transition_counting_ignores_repeats() -> None:
    states = pd.Series(["idle", "running", "running", "throttling", "running", "throttling"])
    assert kpis.count_transitions(states, "throttling") == 2
    assert kpis.count_transitions(states, "running") == 2


def test_pue_is_the_ratio_of_sums_not_the_mean_of_ratios() -> None:
    """Averaging ratios weights a near-idle tick as heavily as a full-load one.
    On this frame the two definitions differ by 30 %."""
    df = pd.DataFrame(
        [
            {"t": 0.0, "dt": 3600.0, "facility_kw": 650.0, "it_drawn_kw": 500.0},
            {"t": 3600.0, "dt": 1.0, "facility_kw": 12.0, "it_drawn_kw": 6.0},
        ]
    )
    ratio_of_sums = kpis.energy_kwh(df, "facility_kw") / kpis.energy_kwh(df, "it_drawn_kw")
    mean_of_ratios = float((df["facility_kw"] / df["it_drawn_kw"]).mean())

    assert ratio_of_sums == pytest.approx(1.3, abs=0.001)
    assert mean_of_ratios == pytest.approx(1.65, abs=0.001)


def test_reported_pue_uses_the_ratio_of_sums_on_a_real_run() -> None:
    result = run_scenario("grid_outage_gen_fail")
    df = result.facility
    ratio_of_sums = kpis.energy_kwh(df, "facility_kw") / kpis.energy_kwh(df, "it_drawn_kw")
    assert result.kpis["pue_avg"] == pytest.approx(round(ratio_of_sums, 4))


def test_instantaneous_pue_is_null_rather_than_infinite_when_nothing_is_served() -> None:
    """`json.dumps` emits bare `Infinity`, which `JSON.parse` rejects — the
    visualization would load blank."""
    result = run_scenario("grid_outage_gen_fail")
    pue = result.facility["pue"]

    assert pue.isna().any(), "expected at least one undefined-PUE tick in a blackout"
    assert not (pue.astype(float).abs() == float("inf")).any()


def test_pue_stays_physical_on_every_scenario() -> None:
    for name in ("normal", "load_spike", "side_a_lost"):
        result = run_scenario(name, duration_s=3600.0)
        assert 1.0 < float(result.kpis["pue_avg"]) < 2.0, name


def test_served_percentage_captures_degradation_that_uptime_misses() -> None:
    result = run_scenario("undersized_cords")
    assert result.kpis["uptime_pct"] == 100.0
    assert float(result.kpis["served_pct"]) < 90.0
    assert float(result.kpis["unserved_energy_kwh"]) > 0.0


def test_diesel_litres_are_the_integral_of_the_burn_rate() -> None:
    result = run_scenario("grid_outage_gen_ok")
    df = result.facility
    integrated = float((df["gen_fuel_rate_l_per_h"] * df["dt"]).sum() / 3600.0)
    assert float(result.kpis["diesel_l"]) == pytest.approx(integrated, abs=1e-3)
    assert float(result.kpis["diesel_l"]) > 0.0
