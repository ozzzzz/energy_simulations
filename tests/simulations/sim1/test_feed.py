"""The 2N core: proportional sharing, and failover with no failover code."""

import pytest

from app.simulations.sim1.electrical.ats import AutomaticTransferSwitch
from app.simulations.sim1.electrical.battery import BatteryString
from app.simulations.sim1.electrical.feed import Feed, split_2n
from app.simulations.sim1.electrical.generator import GenBus
from app.simulations.sim1.electrical.grid import GridFeed, GridState
from app.simulations.sim1.electrical.pdu import Pdu
from app.simulations.sim1.electrical.transformer import Transformer
from app.simulations.sim1.electrical.ups import Ups, UpsState
from app.simulations.sim1.protocols import TickContext

DEAD_BUS = GenBus(running=False, available_kw=0.0, total_ask_kw=0.0, output_kw=0.0)


def _feed(side: str) -> Feed:
    return Feed(
        side=side,
        grid=GridFeed(name=f"grid-{side}"),
        transformer=Transformer(name=f"tx-{side}"),
        ats=AutomaticTransferSwitch(name=f"ats-{side}"),
        ups=Ups(name=f"ups-{side}", battery=BatteryString(name=f"batt-{side}")),
        pdu=Pdu(name=f"pdu-{side}"),
    )


def _ctx(t: float = 0.0, dt: float = 1.0) -> TickContext:
    return TickContext(t=t, dt=dt, tick=int(t / dt))


def _serve(a: Feed, b: Feed, ctx: TickContext, demand_kw: float, cord_limit_kw: float = 900.0):
    cap_a = a.probe(ctx, generator_kw=0.0)
    cap_b = b.probe(ctx, generator_kw=0.0)
    want_a, want_b = split_2n(cap_a, cap_b, demand_kw, cord_limit_kw)
    a.request(ctx, want_a)
    b.request(ctx, want_b)
    return a.deliver(ctx, DEAD_BUS), b.deliver(ctx, DEAD_BUS)


def test_split_is_proportional_to_capacity() -> None:
    assert split_2n(750.0, 750.0, 660.0, 900.0) == pytest.approx((330.0, 330.0))
    assert split_2n(750.0, 250.0, 400.0, 900.0) == pytest.approx((300.0, 100.0))
    assert split_2n(0.0, 0.0, 400.0, 900.0) == (0.0, 0.0)
    assert split_2n(750.0, 750.0, 0.0, 900.0) == (0.0, 0.0)


def test_a_dead_side_takes_nothing_without_any_failover_branch() -> None:
    assert split_2n(0.0, 750.0, 660.0, 900.0) == pytest.approx((0.0, 660.0))


def test_the_cord_limit_pushes_the_excess_onto_the_other_side() -> None:
    a, b = split_2n(750.0, 750.0, 800.0, 450.0)
    assert a == pytest.approx(400.0)
    assert b == pytest.approx(400.0)

    a, b = split_2n(0.0, 750.0, 800.0, 450.0)
    assert (a, b) == pytest.approx((0.0, 450.0))


def test_two_healthy_sides_share_the_load_evenly() -> None:
    a, b = _feed("a"), _feed("b")
    ctx = _ctx()
    del_a, del_b = _serve(a, b, ctx, demand_kw=660.0)

    assert del_a.delivered_kw == pytest.approx(del_b.delivered_kw, rel=1e-9)
    assert del_a.delivered_kw + del_b.delivered_kw == pytest.approx(660.0, rel=1e-9)
    assert del_a.battery_out_kw == 0.0
    assert del_b.battery_out_kw == 0.0


def test_a_side_whose_utility_died_this_tick_rides_on_its_own_battery() -> None:
    """The probe phase reads pre-transfer state, so the UPS falls back to its
    battery on the very tick the utility disappears — no lag, no dropped load."""
    a, b = _feed("a"), _feed("b")
    a.grid.state = GridState.OFFLINE
    ctx = _ctx()

    del_a, del_b = _serve(a, b, ctx, demand_kw=660.0)

    assert del_a.grid_kw == 0.0
    assert del_a.battery_out_kw > 0.0
    assert del_a.delivered_kw + del_b.delivered_kw == pytest.approx(660.0, rel=1e-6)
    assert a.ups.state is UpsState.ON_BATTERY


def test_a_side_with_no_utility_and_no_battery_hands_everything_over_in_one_tick() -> None:
    a, b = _feed("a"), _feed("b")
    a.transformer.trip()
    a.ups.battery.soc = a.ups.battery.cutoff_soc
    ctx = _ctx()

    assert a.probe(ctx, generator_kw=0.0) == 0.0
    del_a, del_b = _serve(a, b, ctx, demand_kw=660.0)

    assert del_a.delivered_kw == 0.0
    assert del_b.delivered_kw == pytest.approx(660.0, rel=1e-9)


def test_one_side_at_site_nominal_sits_just_under_its_nameplate() -> None:
    a, b = _feed("a"), _feed("b")
    a.transformer.trip()
    a.ups.battery.soc = a.ups.battery.cutoff_soc
    _serve(a, b, _ctx(), demand_kw=659.0)

    load_pct = b.ups.monitor.load_pct(b.ups.output_kw)
    assert 85.0 < load_pct < 95.0
    assert not b.ups.on_bypass


def test_one_side_at_site_peak_goes_over_its_nameplate_and_is_flagged() -> None:
    a, b = _feed("a"), _feed("b")
    a.transformer.trip()
    a.ups.battery.soc = a.ups.battery.cutoff_soc

    for i in range(120):
        _serve(a, b, _ctx(t=float(i)), demand_kw=754.0)

    assert b.ups.monitor.load_pct(b.ups.output_kw) > 100.0
    assert b.ups.monitor.overload_s >= 60.0
    assert b.ups.state is UpsState.BYPASS


def test_undersized_cords_curtail_the_load_instead_of_failing_over_cleanly() -> None:
    a, b = _feed("a"), _feed("b")
    a.transformer.trip()
    a.ups.battery.soc = a.ups.battery.cutoff_soc

    del_a, del_b = _serve(a, b, _ctx(), demand_kw=660.0, cord_limit_kw=400.0)
    assert del_a.delivered_kw + del_b.delivered_kw == pytest.approx(400.0, rel=1e-6)


def test_every_side_delivery_closes_its_own_energy_balance() -> None:
    a, b = _feed("a"), _feed("b")
    a.grid.state = GridState.BROWNOUT
    a.ups.battery.soc = 0.6
    for i in range(50):
        del_a, del_b = _serve(a, b, _ctx(t=float(i), dt=10.0), demand_kw=700.0)
        assert abs(del_a.residual_kw) < 1e-9
        assert abs(del_b.residual_kw) < 1e-9
