import pytest

from app.simulations.sim0.models import RackState
from app.simulations.sim0.rack import Rack


def test_rack_splits_draw_into_it_power_and_psu_loss() -> None:
    rack = Rack(name="r1", psu_efficiency=0.97)

    row = rack.step(dt=1.0, demand_kw=100.0, power_scale=1.0, cooling_available_kw=175.0)

    assert row["it_kw"] == pytest.approx(row["consumed_kw"] * 0.97)
    assert row["loss_kw"] == pytest.approx(row["consumed_kw"] * 0.03)
    assert row["it_kw"] + row["loss_kw"] == pytest.approx(row["consumed_kw"])


def test_rack_stays_running_when_cooling_keeps_up() -> None:
    rack = Rack(name="r1")

    for _ in range(60):
        row = rack.step(dt=1.0, demand_kw=100.0, power_scale=1.0, cooling_available_kw=175.0)

    assert row["state"] == RackState.RUNNING.value
    assert rack.temp_c == rack.ambient_c


def test_rack_throttles_then_shuts_down_when_cooling_deficient() -> None:
    rack = Rack(name="r1", thermal_mass_kws_per_c=10.0)

    states = []
    for _ in range(200):
        row = rack.step(dt=1.0, demand_kw=150.0, power_scale=1.0, cooling_available_kw=0.0)
        states.append(row["state"])

    assert RackState.THROTTLING.value in states
    assert RackState.EMERGENCY_SHUTDOWN.value in states
    assert states[-1] == RackState.EMERGENCY_SHUTDOWN.value


def test_rack_recovers_after_cooling_returns() -> None:
    rack = Rack(name="r1", thermal_mass_kws_per_c=10.0)

    for _ in range(200):
        rack.step(dt=1.0, demand_kw=150.0, power_scale=1.0, cooling_available_kw=0.0)
    assert rack.state == RackState.EMERGENCY_SHUTDOWN

    last_state = None
    for _ in range(500):
        row = rack.step(dt=1.0, demand_kw=0.0, power_scale=1.0, cooling_available_kw=175.0)
        last_state = row["state"]

    assert last_state != RackState.EMERGENCY_SHUTDOWN.value
