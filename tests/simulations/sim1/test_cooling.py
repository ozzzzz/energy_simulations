import pytest

from app.simulations.sim1.cooling.chiller import Chiller
from app.simulations.sim1.cooling.plant import CoolingPlant
from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.rack import Rack
from app.simulations.sim1.workload import Profile, WorkloadProfile


def _ctx(t: float, dt: float = 30.0) -> TickContext:
    return TickContext(t=t, dt=dt, tick=int(t / dt))


def _plant() -> CoolingPlant:
    return CoolingPlant()


def _racks(n: int = 4) -> list[Rack]:
    return [
        Rack(name=f"rack-{i + 1}", workload=WorkloadProfile(Profile.TRAINING, noise_ratio=0.0, seed=i))
        for i in range(n)
    ]


def _step(plant: CoolingPlant, racks: list[Rack], ctx: TickContext, power_kw: float | None = None) -> None:
    request_kw = plant.request(ctx)
    supply = plant.deliver(ctx, electrical_kw=request_kw if power_kw is None else power_kw)
    liquid = 0.0
    air = 0.0
    for rack in racks:
        rack.apply(
            ctx,
            granted_kw=rack.request(ctx),
            sink_c=supply.sink_c(rack.liquid_capture_rate),
            ua_scale=supply.ua_scale,
        )
        liquid += rack.liquid_kw
        air += rack.air_kw
    plant.observe(ctx, liquid_heat_kw=liquid, air_heat_kw=air)


def test_chiller_electricity_is_removal_divided_by_cop() -> None:
    chiller = Chiller(capacity_kw=600.0, cop=6.0)
    assert chiller.electrical_for(480.0) == pytest.approx(80.0)
    removed = chiller.commit(_ctx(0.0), electrical_kw=80.0, flow_factor=1.0)
    assert removed == pytest.approx(480.0)


def test_a_chiller_with_no_coolant_flow_removes_nothing() -> None:
    """Losing pumps does not degrade cooling proportionally — it stops it, while
    the compressors carry on drawing power."""
    chiller = Chiller()
    assert chiller.commit(_ctx(0.0), electrical_kw=80.0, flow_factor=0.0) == 0.0
    assert chiller.electrical_kw == 80.0


def test_the_loop_warms_at_the_rate_its_water_mass_implies() -> None:
    plant = _plant()
    plant.chiller.fault()
    start_c = plant.loop.supply_c
    heat_kw = 540.0

    plant.observe(_ctx(0.0, dt=60.0), liquid_heat_kw=heat_kw, air_heat_kw=0.0)
    expected_rise = heat_kw * 60.0 / plant.loop.mass_kws_per_c

    assert plant.loop.supply_c - start_c == pytest.approx(expected_rise, rel=1e-9)
    # About 1.5 C per minute at nominal load, which is the pace the whole
    # cooling-failure scenario is read at.
    assert 1.3 < expected_rise < 1.8


def test_a_chiller_fault_warms_the_loop_and_starves_the_racks_together() -> None:
    """The full causal chain in one assertion: capacity lost -> loop warms ->
    rack delta-T shrinks -> less heat can leave the racks."""
    plant = _plant()
    racks = _racks()
    for i in range(40):
        _step(plant, racks, _ctx(i * 30.0))

    plant.chiller.fault()
    loop_temps: list[float] = []
    removals: list[float] = []
    for i in range(40, 120):
        _step(plant, racks, _ctx(i * 30.0))
        loop_temps.append(plant.loop.supply_c)
        removals.append(sum(rack.removed_kw for rack in racks))

    assert loop_temps == sorted(loop_temps), "loop temperature should rise monotonically"
    assert removals[-1] < removals[0], "rack heat rejection should fall as the loop warms"
    assert plant.chiller.electrical_kw == 0.0, "a tripped chiller stops drawing power"


def test_pumps_are_served_before_compressors() -> None:
    plant = _plant()
    ctx = _ctx(0.0)
    plant.request(ctx)
    supply = plant.deliver(ctx, electrical_kw=plant.cdu.pump_demand_kw * 0.5)

    assert supply.pump_kw == pytest.approx(plant.cdu.pump_demand_kw * 0.5)
    assert supply.chiller_kw == 0.0
    assert supply.ua_scale == pytest.approx(0.5)


def test_the_sink_temperature_blends_both_cooling_channels() -> None:
    plant = _plant()
    ctx = _ctx(0.0)
    plant.request(ctx)
    supply = plant.deliver(ctx, electrical_kw=200.0)

    blended = supply.sink_c(liquid_capture_rate=0.9)
    assert blended == pytest.approx(0.9 * supply.supply_c + 0.1 * supply.room_c)
    assert supply.sink_c(1.0) == pytest.approx(supply.supply_c)


def test_the_control_lag_damps_the_cooling_power_feedback_loop() -> None:
    """Heat up -> chiller draw up -> less power for IT -> heat down is a two-tick
    limit cycle without a first-order lag on the chiller's demand."""
    plant = _plant()
    ctx = _ctx(0.0, dt=60.0)
    plant.liquid_heat_kw = 500.0
    first = plant.request(ctx)
    plant.liquid_heat_kw = 0.0
    second = plant.request(ctx)

    assert first < plant.chiller.electrical_for(500.0) + plant.cdu.pump_demand_kw
    assert second > plant.cdu.pump_demand_kw, "demand should decay, not snap to zero"


def test_stored_heat_is_reported_for_the_conservation_check() -> None:
    plant = _plant()
    baseline = plant.stored_kws
    plant.observe(_ctx(0.0, dt=60.0), liquid_heat_kw=500.0, air_heat_kw=50.0)
    assert plant.stored_kws > baseline
