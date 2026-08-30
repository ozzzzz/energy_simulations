"""Pins the worked tick quoted in `docs/sim_1_plan.md` §2.2.

That section walks one tick with real numbers, which is the clearest explanation of
the model in the docs — and the easiest thing to get quietly wrong, since several of
its figures were once derived by hand and were off. If a model change moves any of
these, the doc needs updating and this test says so.

Deliberately a **normal** tick: nothing broken, nothing failing. Failure behaviour is
documented separately in §5 and tested by the scenario tests.
"""

import pytest

from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.scenarios import build_facility, get_scenario

DT = 60.0
TICK_T = 14 * 3600.0 + 1800.0
"""14:30 — the daily traffic peak, and mid-cycle so the training racks are not in a
checkpoint dip."""


@pytest.fixture(scope="module")
def traced() -> dict:
    """The documented tick, run exactly once with everything the doc quotes captured.

    Handing tests a live facility would let each of them advance it again.
    """
    facility = build_facility(get_scenario("normal"))
    for i in range(int(TICK_T / DT)):
        facility.tick(TickContext(t=i * DT, dt=DT, tick=i))

    ctx = TickContext(t=TICK_T, dt=DT, tick=int(TICK_T / DT))
    side = facility.side_a

    # Step 1 changes nothing, so it can be measured before the tick proper.
    grid_cap = side.grid.probe(ctx)
    tx_cap = side.transformer.probe(ctx, grid_cap)
    ats_cap = side.ats.probe(ctx, primary_kw=tx_cap, generator_kw=facility.genset.probe(ctx))
    ups_cap = side.ups.probe(ctx, ats_cap)
    pdu_cap = side.pdu.probe(ctx, ups_cap)

    row = facility.tick(ctx)

    tx_loss = side.transformer.loss_at(side.transformer.throughput_kw)
    return {
        "row": row,
        "capacity": [grid_cap, tx_cap, ats_cap, ups_cap, pdu_cap],
        "tx_loss": tx_loss,
        "ups_loss": side.transformer.throughput_kw - tx_loss - side.ups.output_kw,
        "pdu_loss": side.ups.output_kw - side.pdu.output_kw,
        "racks": {rack.name: rack for rack in facility.racks},
        "kw_for_share": facility.racks[1].kw_for(30_595.0),
    }


def test_step_one_capacity_chain(traced) -> None:
    grid, tx, ats, ups, pdu = traced["capacity"]
    assert grid == pytest.approx(1500.0, abs=0.05)
    assert tx == pytest.approx(990.5, abs=0.05)
    assert ats == pytest.approx(990.5, abs=0.05)
    assert ups == pytest.approx(937.5, abs=0.05)
    assert pdu == pytest.approx(796.0, abs=0.05)


def test_step_two_demand(traced) -> None:
    row = traced["row"]
    assert row["user_offered_rps"] == pytest.approx(145.7, abs=0.05)
    assert row["it_demand_kw"] == pytest.approx(523.0, abs=0.05)
    assert row["mech_demand_kw"] == pytest.approx(114.2, abs=0.05)
    assert traced["kw_for_share"] == pytest.approx(123.3, abs=0.05)

    racks = traced["racks"]
    assert racks["rack-2"].demand_kw == pytest.approx(123.3, abs=0.05)
    assert racks["rack-1"].demand_kw == pytest.approx(136.6, abs=0.05)
    assert racks["rack-4"].demand_kw == pytest.approx(139.8, abs=0.05)


def test_step_three_delivery_and_losses(traced) -> None:
    row = traced["row"]
    assert row["a_grid_kw"] == pytest.approx(334.1, abs=0.05)
    assert row["a_delivered_kw"] == pytest.approx(318.6, abs=0.05)
    assert row["b_delivered_kw"] == pytest.approx(318.6, abs=0.05)
    assert row["loss_kw"] == pytest.approx(31.0, abs=0.05)

    assert traced["tx_loss"] == pytest.approx(2.4, abs=0.05)
    assert traced["ups_loss"] == pytest.approx(11.5, abs=0.05)
    assert traced["pdu_loss"] == pytest.approx(1.6, abs=0.05)
    # Nothing is stressed on a normal tick.
    assert row["a_ups_load_pct"] < 50.0
    assert row["a_batt_soc"] == pytest.approx(1.0)


def test_step_four_heat_and_throughput(traced) -> None:
    row = traced["row"]
    assert row["cool_chiller_electrical_kw"] == pytest.approx(76.2, abs=0.05)
    assert row["cool_cdu_pump_kw"] == pytest.approx(20.0, abs=0.05)
    assert row["cool_crah_electrical_kw"] == pytest.approx(18.0, abs=0.05)
    assert row["cool_liquid_heat_kw"] == pytest.approx(463.8, abs=0.05)
    assert row["cool_air_heat_kw"] == pytest.approx(51.5, abs=0.05)
    # Rejection is the chiller's electrical draw times its COP.
    assert row["cool_rejected_liquid_kw"] == pytest.approx(76.2 * 6.0, abs=0.5)
    assert row["cool_loop_supply_c"] == pytest.approx(17.9, abs=0.05)

    racks = traced["racks"]
    warming = racks["rack-2"]
    assert warming.drawn_kw == pytest.approx(123.3, abs=0.05)
    assert warming.removed_kw == pytest.approx(119.6, abs=0.05)
    assert warming.temp_c == pytest.approx(41.7, abs=0.05)
    # The rest went into the rack's own thermal mass.
    assert warming.drawn_kw - warming.removed_kw == pytest.approx(3.7, abs=0.05)
    # rack-1 is cooling after a checkpoint dip, so it sheds more than it drew.
    cooling = racks["rack-1"]
    assert cooling.removed_kw - cooling.drawn_kw == pytest.approx(1.6, abs=0.05)

    assert row["user_served_rps"] == pytest.approx(145.7, abs=0.05)
    assert row["user_queued_requests"] == pytest.approx(0.0, abs=0.01)


def test_the_documented_books_balance(traced) -> None:
    row = traced["row"]
    assert row["facility_kw"] == pytest.approx(668.2, abs=0.05)
    assert row["it_drawn_kw"] + row["mech_kw"] + row["loss_kw"] == pytest.approx(668.2, abs=0.05)
    assert row["balance_residual_kw"] == pytest.approx(0.0, abs=1e-9)
    assert row["pue"] == pytest.approx(1.278, abs=0.001)
