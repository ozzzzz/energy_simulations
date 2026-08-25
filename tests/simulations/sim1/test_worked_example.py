"""Pins the worked tick quoted in `docs/sim_1_plan.md` §2.1.

That section walks one real tick with real numbers, which is the clearest
explanation of the model in the docs — and the easiest thing to get quietly wrong,
since three of its figures were originally derived by hand and were off. If a model
change moves any of these, the doc needs updating and this test says so.
"""

import pytest

from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.scenarios import build_facility, get_scenario

DT = 30.0
TICK_T = 600.0


@pytest.fixture(scope="module")
def traced() -> dict:
    """The documented tick: `cooling_failure` at t=600 s, before the chiller trips.

    Ticked exactly once, with everything the doc quotes captured into a plain dict
    — handing tests a live facility would let each of them advance it again.
    """
    facility = build_facility(get_scenario("cooling_failure"))
    for i in range(20):
        facility.tick(TickContext(t=i * DT, dt=DT, tick=i))

    ctx = TickContext(t=TICK_T, dt=DT, tick=20)
    side = facility.side_a

    # Phase 1 is non-mutating, so it can be measured before the tick proper.
    grid_cap = side.grid.probe(ctx)
    tx_cap = side.transformer.probe(ctx, grid_cap)
    ats_cap = side.ats.probe(ctx, primary_kw=tx_cap, generator_kw=facility.genset.probe(ctx))
    ups_cap = side.ups.probe(ctx, ats_cap)
    pdu_cap = side.pdu.probe(ctx, ups_cap)

    row = facility.tick(ctx)

    tx_loss = side.transformer.loss_at(side.transformer.throughput_kw)
    rack = facility.racks[3]
    interactive = facility.racks[1]
    return {
        "row": row,
        "capacity": [grid_cap, tx_cap, ats_cap, ups_cap, pdu_cap],
        "tx_loss": tx_loss,
        "ups_loss": side.transformer.throughput_kw - tx_loss - side.ups.output_kw,
        "pdu_loss": side.ups.output_kw - side.pdu.output_kw,
        "rack_drawn": rack.drawn_kw,
        "rack_removed": rack.removed_kw,
        "rack_temp": rack.temp_c,
        "kw_for_29906": interactive.kw_for(29_906.0),
        "tokens_at_121": interactive.tokens_per_s_for(121.0),
    }


def test_pass_one_capacity_chain(traced) -> None:
    grid, tx, ats, ups, pdu = traced["capacity"]
    assert grid == pytest.approx(1500.0, abs=0.05)
    assert tx == pytest.approx(990.5, abs=0.05)
    assert ats == pytest.approx(990.5, abs=0.05)
    assert ups == pytest.approx(937.5, abs=0.05)
    assert pdu == pytest.approx(796.0, abs=0.05)


def test_pass_two_demand(traced) -> None:
    row = traced["row"]
    assert row["user_offered_rps"] == pytest.approx(142.4, abs=0.05)
    assert row["it_demand_kw"] == pytest.approx(501.1, abs=0.05)
    assert row["mech_demand_kw"] == pytest.approx(127.7, abs=0.05)
    assert traced["kw_for_29906"] == pytest.approx(121.0, abs=0.05)
    assert traced["tokens_at_121"] == pytest.approx(29_906.0, abs=2.0)


def test_pass_three_delivery_and_losses(traced) -> None:
    row = traced["row"]
    assert row["a_grid_kw"] == pytest.approx(329.7, abs=0.05)
    assert row["a_delivered_kw"] == pytest.approx(314.4, abs=0.05)
    assert row["b_delivered_kw"] == pytest.approx(314.4, abs=0.05)
    assert row["loss_kw"] == pytest.approx(30.6, abs=0.05)
    assert row["loss_kw"] / 2 == pytest.approx(15.3, abs=0.05)

    assert traced["tx_loss"] == pytest.approx(2.4, abs=0.05)
    assert traced["ups_loss"] == pytest.approx(11.4, abs=0.05)
    assert traced["pdu_loss"] == pytest.approx(1.6, abs=0.05)


def test_pass_four_heat_and_throughput(traced) -> None:
    row = traced["row"]
    assert row["cool_chiller_electrical_kw"] == pytest.approx(89.7, abs=0.05)
    assert row["cool_cdu_pump_kw"] == pytest.approx(20.0, abs=0.05)
    assert row["cool_crah_electrical_kw"] == pytest.approx(18.0, abs=0.05)
    assert row["cool_liquid_heat_kw"] == pytest.approx(451.0, abs=0.05)
    assert row["cool_air_heat_kw"] == pytest.approx(50.1, abs=0.05)
    # Rejection is the chiller's electrical draw times its COP.
    assert row["cool_rejected_liquid_kw"] == pytest.approx(89.7 * 6.0, abs=0.5)
    assert row["cool_loop_supply_c"] == pytest.approx(19.24, abs=0.01)

    assert traced["rack_drawn"] == pytest.approx(133.5, abs=0.05)
    assert traced["rack_removed"] == pytest.approx(128.0, abs=0.05)
    assert traced["rack_temp"] == pytest.approx(44.7, abs=0.05)
    # The rest went into the rack's own thermal mass.
    assert traced["rack_drawn"] - traced["rack_removed"] == pytest.approx(5.5, abs=0.05)

    assert row["user_served_rps"] == pytest.approx(142.4, abs=0.05)
    assert row["user_queued_requests"] == pytest.approx(0.0, abs=0.01)


def test_the_documented_books_balance(traced) -> None:
    row = traced["row"]
    assert row["sources_kw"] == pytest.approx(659.4, abs=0.05)
    assert row["it_drawn_kw"] + row["mech_kw"] + row["loss_kw"] == pytest.approx(659.4, abs=0.05)
    assert row["balance_residual_kw"] == pytest.approx(0.0, abs=1e-9)
    assert row["pue"] == pytest.approx(1.316, abs=0.001)
