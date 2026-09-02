import pytest

from app.simulations.sim1.electrical.battery import BatteryString
from app.simulations.sim1.electrical.ups import Ups, UpsState
from app.simulations.sim1.models import TickContext


def _ups(**kwargs: float) -> Ups:
    return Ups(name="ups", battery=BatteryString(name="batt", capacity_kwh=60.0), **kwargs)  # type: ignore[arg-type]


def _ctx(t: float = 0.0, dt: float = 1.0) -> TickContext:
    return TickContext(t=t, dt=dt, tick=int(t / dt))


def test_the_delivery_identity_holds_on_the_mains_path() -> None:
    ups = _ups()
    delivery = ups.deliver(_ctx(), supply_kw=400.0, demand_kw=380.0)
    assert abs(delivery.residual_kw) < 1e-12
    assert ups.state is UpsState.ONLINE


def test_the_delivery_identity_holds_while_the_battery_carries_the_load() -> None:
    ups = _ups()
    delivery = ups.deliver(_ctx(), supply_kw=0.0, demand_kw=300.0)
    assert abs(delivery.residual_kw) < 1e-12
    assert delivery.injected_kw == pytest.approx(300.0)
    assert ups.state is UpsState.ON_BATTERY


def test_the_delivery_identity_holds_while_charging() -> None:
    ups = _ups()
    ups.battery.soc = 0.5
    delivery = ups.deliver(_ctx(dt=60.0), supply_kw=500.0, demand_kw=300.0, charge_kw=80.0)
    assert abs(delivery.residual_kw) < 1e-12
    assert delivery.stored_kw > 0.0


def test_the_battery_covers_only_the_gap_the_mains_leaves() -> None:
    ups = _ups()
    delivery = ups.deliver(_ctx(), supply_kw=100.0, demand_kw=300.0)
    served_from_mains = 100.0 * ups.double_conversion_efficiency
    assert delivery.injected_kw == pytest.approx(300.0 - served_from_mains)
    assert delivery.delivered_kw == pytest.approx(300.0)


def test_output_falls_to_zero_once_the_battery_reaches_its_cutoff() -> None:
    ups = _ups()
    ups.battery.soc = ups.battery.cutoff_soc
    delivery = ups.deliver(_ctx(), supply_kw=0.0, demand_kw=300.0)
    assert delivery.delivered_kw == 0.0
    assert ups.state is UpsState.OFFLINE


def test_recharge_never_steals_input_from_the_critical_load() -> None:
    """Otherwise two 100 kW chargers coming online push a freshly started
    generator over its rating, and the failure looks exactly like a real one."""
    ups = _ups()
    ups.battery.soc = 0.5
    delivery = ups.deliver(_ctx(dt=60.0), supply_kw=310.0, demand_kw=300.0, charge_kw=100.0)

    assert delivery.delivered_kw == pytest.approx(300.0)
    assert delivery.stored_kw < 100.0


def test_sustained_overload_transfers_to_bypass_rather_than_dropping_the_load() -> None:
    """The most instructive behaviour in the electrical model: the site survives
    the overload but silently loses battery protection."""
    ups = _ups(rating_kw=100.0)
    ups.monitor.hold_limit_s = 60.0

    for i in range(120):
        ups.deliver(_ctx(t=float(i)), supply_kw=200.0, demand_kw=105.0)

    assert ups.state is UpsState.BYPASS
    assert ups.on_bypass
    assert ups.monitor.overload_s >= 60.0
    # Still serving the load, which is the point.
    assert ups.output_kw == pytest.approx(105.0, rel=1e-3)


def test_bypass_is_near_lossless_and_offers_no_battery_support() -> None:
    ups = _ups(rating_kw=100.0)
    ups.monitor.state = ups.monitor.state.TRIPPED
    delivery = ups.deliver(_ctx(), supply_kw=0.0, demand_kw=50.0)
    assert delivery.delivered_kw == 0.0
    assert delivery.injected_kw == 0.0

    delivery = ups.deliver(_ctx(), supply_kw=60.0, demand_kw=50.0)
    assert delivery.loss_kw == pytest.approx(50.0 * (1.0 / ups.bypass_efficiency - 1.0))


def test_capacity_may_exceed_the_nameplate_for_a_short_while() -> None:
    """A UPS with a 125 % overload rating can deliver 125 %; clipping at 100 %
    would curtail the load and make the bypass transfer unreachable."""
    ups = _ups(rating_kw=100.0)
    assert ups.probe(_ctx(), upstream_kw=1000.0) == pytest.approx(125.0)
