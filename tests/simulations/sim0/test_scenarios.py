import pytest

from app.simulations.sim0.engine import run_scenario
from app.simulations.sim0.scenarios import get_scenario


def test_mixed_scenario_assigns_rack_1_to_training_and_the_rest_to_inference() -> None:
    scenario = get_scenario("mixed")
    workloads = scenario.build_workloads()
    assert [w.profile for w in workloads] == ["training", "inference", "inference", "inference"]


def test_inference_scenario_never_throttles_or_shuts_down() -> None:
    df, kpis = run_scenario("inference", duration_s=120.0, dt=1.0)

    assert kpis["uptime_pct"] == 100.0
    assert kpis["throttle_events"] == 0
    assert kpis["shutdown_events"] == 0
    assert not df.empty


def test_training_scenario_holds_near_peak_without_shutdown() -> None:
    _df, kpis = run_scenario("training", duration_s=600.0, dt=1.0)

    assert kpis["uptime_pct"] == 100.0
    assert kpis["shutdown_events"] == 0


def test_delivery_efficiency_matches_default_psu_efficiency() -> None:
    _df, kpis = run_scenario("training", duration_s=600.0, dt=1.0)

    assert kpis["delivery_efficiency_pct"] == pytest.approx(97.0, abs=0.01)


def test_unknown_scenario_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        run_scenario("bogus", duration_s=10.0)
