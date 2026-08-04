import statistics

import pytest

from app.simulations.sim0.inputs import CoolingInput, PowerInput, WorkloadInput, _diurnal_factor

PEAK_T = 14 * 3600.0  # 14:00, configured daily peak
TROUGH_T = 2 * 3600.0  # 02:00, trough (12h after the peak)


def test_diurnal_factor_peaks_at_configured_hour_and_troughs_12h_later() -> None:
    assert _diurnal_factor(PEAK_T) == pytest.approx(1.0)
    assert _diurnal_factor(TROUGH_T) == pytest.approx(0.45)


def test_idle_profile_is_low_and_constant() -> None:
    workload = WorkloadInput("idle")
    assert workload._base_kw(0.0) == workload._base_kw(1000.0) == 20.0


def test_inference_profile_oscillates_between_base_and_spike_at_peak_hour() -> None:
    workload = WorkloadInput("inference")
    values = {round(workload._base_kw(PEAK_T + t), 1) for t in range(0, 60)}
    assert values == {90.0, 140.0}


def test_inference_profile_is_lower_at_night_than_during_the_day() -> None:
    workload = WorkloadInput("inference")
    day_values = [workload._base_kw(PEAK_T + t) for t in range(30)]
    night_values = [workload._base_kw(TROUGH_T + t) for t in range(30)]
    assert min(day_values) > max(night_values)


def test_training_profile_is_near_peak_with_occasional_dip() -> None:
    workload = WorkloadInput("training")
    values = {workload._base_kw(t) for t in range(0, 600, 5)}
    assert values == {100.0, 150.0}
    # sustained near-peak: dips are rare relative to the full period
    high_count = sum(1 for t in range(0, 300) if workload._base_kw(t) == 150.0)
    assert high_count > 250


def test_training_profile_does_not_follow_the_day_night_cycle() -> None:
    # batch jobs don't sleep: same phase of the dip cycle gives the same value day or night
    workload = WorkloadInput("training")
    assert {workload._base_kw(PEAK_T + i) for i in range(300)} == {workload._base_kw(TROUGH_T + i) for i in range(300)}


def test_mixed_profile_switches_phases_over_time() -> None:
    workload = WorkloadInput("mixed")
    assert workload._base_kw(10.0) == 20.0
    assert workload._base_kw(100.0) in {100.0, 150.0}
    # inference phase (t >= 300) still follows the day/night cycle
    assert workload._base_kw(PEAK_T) > workload._base_kw(TROUGH_T)
    assert round(workload._base_kw(PEAK_T), 1) in {90.0, 140.0}


def test_unknown_profile_raises() -> None:
    with pytest.raises(ValueError):
        WorkloadInput("bogus").demand_kw(0.0)


def test_demand_kw_jitters_around_the_base_shape() -> None:
    workload = WorkloadInput("inference", seed=7)
    values = [workload.demand_kw(PEAK_T + t) for t in range(60)]

    # not a flat line: consecutive ticks at the same base level still differ
    assert len({round(v, 3) for v in values}) > 10
    assert all(v >= 0.0 for v in values)
    assert 85.0 < statistics.mean(values) < 115.0  # base is 90 5/6 of the time, 140 1/6


def test_demand_kw_is_reproducible_for_a_given_seed() -> None:
    a = WorkloadInput("training", seed=42)
    b = WorkloadInput("training", seed=42)
    assert [a.demand_kw(t) for t in range(30)] == [b.demand_kw(t) for t in range(30)]


def test_power_input_is_constant() -> None:
    power = PowerInput(750.0)
    assert power.step(t=0.0, dt=1.0, requested_kw=0.0) == power.step(t=9999.0, dt=1.0, requested_kw=500.0) == 750.0


def test_cooling_input_splits_liquid_and_air() -> None:
    cooling = CoolingInput(liquid_capacity_kw=600.0, air_capacity_kw=100.0)
    assert cooling.liquid_step(0.0, 1.0, 0.0) == cooling.liquid_step(9999.0, 1.0, 0.0) == 600.0
    assert cooling.air_step(0.0, 1.0, 0.0) == cooling.air_step(9999.0, 1.0, 0.0) == 100.0
    assert cooling.step(0.0, 1.0, 0.0) == 700.0


def test_cooling_input_failure_window_cuts_capacity() -> None:
    cooling = CoolingInput(
        liquid_capacity_kw=600.0,
        air_capacity_kw=100.0,
        failure_start_s=100.0,
        failure_end_s=200.0,
        failure_severity=0.85,
    )
    assert cooling.step(0.0, 1.0, 0.0) == 700.0
    assert cooling.step(150.0, 1.0, 0.0) == pytest.approx(700.0 * 0.15)
    assert cooling.step(200.0, 1.0, 0.0) == 700.0
